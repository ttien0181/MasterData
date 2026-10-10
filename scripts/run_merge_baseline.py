"""Run baseline tests with isolated local state, never developer DB/R2/LLM settings.

Use the Python interpreter from a dedicated virtual environment. Reports are
generated under ignored artifacts/merge-baseline/<timestamp>-<unique suffix>.
This is a diagnostic runner, not a production configuration or migration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReportStateDirectory(tempfile.TemporaryDirectory):
    """Clean only owned report state, including long content-addressed keys."""

    @classmethod
    def _rmtree(cls, name, ignore_errors=False, repeated=False):
        value = str(name)
        plain = value[4:] if value.startswith("\\\\?\\") else value
        resolved = Path(plain).resolve()
        if not resolved.is_relative_to(ROOT / "artifacts" / "merge-baseline"):
            raise RuntimeError("Refusing cleanup outside baseline report workspace")
        if os.name == "nt" and not value.startswith("\\\\?\\"):
            value = "\\\\?\\" + str(resolved)
        return super()._rmtree(value, ignore_errors=ignore_errors, repeated=repeated)
FOCUSED = (
    "backend/tests/test_api",
    "backend/tests/test_mdm",
    "backend/tests/test_storage_registry.py",
    "backend/tests/test_mdm_stage_registry.py",
    "backend/tests/test_pipeline_workers.py",
    "backend/tests/test_object_store.py",
)


def isolated_environment(directory: Path) -> dict[str, str]:
    env = os.environ.copy()
    scratch = directory / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    database = "sqlite:///" + (directory / "test.db").as_posix()
    env.update({
        "APP_ENV": "test",
        # Explicit origins used by browser-origin contract tests. Keep the
        # production/test allowlist strict; never enable the development regex.
        "CORS_ORIGINS": "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000",
        "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "backend" / "src"), str(ROOT / "backend"))),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
        "MDM_DATABASE_PROFILE": "local",
        "DATABASE_URL": database,
        "MDM_DATABASE_URL": database,
        "MDM_DATABASE_URL_LOCAL": database,
        "UPLOAD_STAGING_DIR": str(directory / "uploads"),
        "EXPORT_ARTIFACT_DIR": str(directory / "exports"),
        "EXPORT_BATCH_ROWS": "512",
        "EXPORT_MAX_GROUP_RECORDS": "2048",
        "EXPORT_DISK_BYTES": str(2 * 1024**3),
        "EXPORT_MAX_RSS_BYTES": "0",
        "EXPORT_TIMEOUT_SECONDS": "3600",
        "EXPORT_PARQUET_ENABLED": "false",
        "AUTH_CSRF_SECRET": "baseline-local-only-not-a-production-secret",
        "OPENAI_API_KEY": "baseline-not-a-real-api-key",
        "GEMINI_API_KEY": "",
        "GEMINI_API_KEY_1": "",
        "GEMINI_API_KEY_2": "",
        "GEMINI_API_KEY_3": "",
        "OPENROUTER_API_KEY": "",
        "LLM_ENABLED": "false",
        "LLM_SEMANTIC_ENABLED": "false",
        "LLM_EXPLANATION_ENABLED": "false",
        "SEMANTIC_ADDRESS_ENABLED": "false",
        "DECISION_SEMANTICS_V2_ENABLED": "false",
        "CLUSTER_SAFETY_V2_ENABLED": "false",
        "GENERATION_STAGING_ENABLED": "false",
        "MDM_ENGINE_V2_ENABLED": "false",
        "PIPELINE_RESUME_ENABLED": "false",
        "STREAM_PREPARE_NORMALIZE_ENABLED": "false",
        "STREAM_NORMALIZE_BUILD_ENABLED": "false",
        "STREAM_BUILD_SCORE_ENABLED": "false",
        "STREAM_SCORE_DECIDE_ENABLED": "false",
        "STREAM_DECIDE_MATERIALIZE_ENABLED": "false",
        "R2_ENDPOINT_URL": "",
        "R2_ACCESS_KEY_ID": "",
        "R2_SECRET_ACCESS_KEY": "",
        "R2_BUCKET": "",
        "AWS_EC2_METADATA_DISABLED": "true",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_API_KEY": "",
        "LANGSMITH_API_KEY": "",
    })
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("exports", "contracts", "input", "candidates", "scoring", "decisions", "clustering", "generation", "adapter", "worker", "artifacts", "streaming", "resume", "focused", "all"), default="focused")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    reports = ROOT / "artifacts" / "merge-baseline"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = Path(tempfile.mkdtemp(prefix=stamp + "-", dir=reports))
    if args.suite == "all":
        paths = ["backend/tests"]
    elif args.suite == "exports":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_export_stream.py", "test_resolution_items.py", "test_document_pipeline.py",
            "test_engine_adapter.py", "test_upload_sessions.py", "test_vertical_slice.py",
            "test_merge_baseline_contracts.py", "test_streaming.py")]
    elif args.suite == "resume":
        paths = ["backend/tests/test_mdm/test_checkpoint_resume.py"]
    elif args.suite == "streaming":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_streaming.py", "test_scoring_engine.py", "test_generation_staging.py", "test_engine_adapter.py", "test_job_queue.py")]
        paths.append("backend/tests/test_partition_artifacts.py")
    elif args.suite == "artifacts":
        paths = ["backend/tests/test_partition_artifacts.py", "backend/tests/test_object_store.py",
                 "backend/tests/test_storage_registry.py", "backend/tests/test_mdm_stage_registry.py",
                 "backend/tests/test_mdm/test_job_queue.py", "backend/tests/test_mdm/test_generation_staging.py"]
    elif args.suite == "worker":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_job_queue.py", "test_generation_staging.py", "test_migrations.py",
            "test_engine_adapter.py", "test_document_pipeline.py", "test_merge_baseline_contracts.py")]
    elif args.suite == "contracts":
        paths = ["backend/tests/test_mdm/test_merge_baseline_contracts.py"]
    elif args.suite == "candidates":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_candidate_engine.py", "test_tfidf_blocking.py", "test_sparse_tfidf.py", "test_merge_baseline_contracts.py")]
    elif args.suite == "scoring":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_scoring_engine.py", "test_candidate_engine.py", "test_merge_baseline_contracts.py",
            "test_hard_constraints_and_floor.py", "test_policy_yaml.py", "test_policy_engine.py",
            "test_regression_t1_t20.py", "test_regression_t21_t28.py")]
    elif args.suite == "decisions":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_decision_semantics.py", "test_hard_constraints_and_floor.py",
            "test_cluster_guards.py", "test_policy_yaml.py", "test_policy_engine.py",
            "test_regression_t1_t20.py", "test_regression_t21_t28.py",
            "test_resolution_items.py", "test_document_pipeline.py", "test_vertical_slice.py",
            "test_scoring_engine.py", "test_merge_baseline_contracts.py")]
    elif args.suite == "clustering":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_safe_clustering.py", "test_golden_and_benchmark.py", "test_cluster_guards.py",
            "test_decision_semantics.py", "test_resolution_items.py", "test_document_pipeline.py",
            "test_scoring_engine.py", "test_merge_baseline_contracts.py")]
    elif args.suite == "adapter":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_engine_adapter.py", "test_merge_baseline_contracts.py", "test_resolution_items.py",
            "test_document_pipeline.py", "test_vertical_slice.py", "test_golden_and_benchmark.py",
            "test_generation_staging.py", "test_decision_semantics.py", "test_migrations.py")]
    elif args.suite == "generation":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_generation_staging.py", "test_migrations.py", "test_input_contract.py",
            "test_safe_clustering.py", "test_decision_semantics.py", "test_document_pipeline.py",
            "test_resolution_items.py", "test_merge_baseline_contracts.py")]
    elif args.suite == "input":
        paths = ["backend/tests/test_mdm/" + name for name in (
            "test_input_contract.py", "test_resolution_import_schemas.py", "test_upload_sessions.py",
            "test_migrations.py", "test_document_pipeline.py", "test_merge_baseline_contracts.py")]
    else:
        paths = list(FOCUSED)
    with ReportStateDirectory(prefix="state-", dir=report) as state:
        command = [sys.executable, "-X", "utf8", "-m", "pytest", *paths,
                   "-p", "pytest_asyncio.plugin", "-p", "tests.merge_baseline_plugin",
                   "-q", "--tb=short",
                   "--junitxml=" + str(report / "pytest.xml"),
                   "--basetemp=" + str(Path(state) / "pytest")]
        env = isolated_environment(Path(state))
        print("Baseline report:", report, flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    timeout=args.timeout, check=False)
            output = result.stdout + result.stderr
            code = result.returncode
        except subprocess.TimeoutExpired as error:
            def decode(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            output = decode(error.stdout) + decode(error.stderr) + "\nBASELINE_TIMEOUT\n"
            code = 124
        (report / "pytest.log").write_text(output, encoding="utf-8")
        freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=False)
        if freeze.returncode:
            raise RuntimeError("Could not record dependency versions: " + freeze.stderr)
        (report / "dependencies.txt").write_text(freeze.stdout, encoding="utf-8")
        (report / "run.json").write_text(json.dumps({
            "suite": args.suite, "command": command, "exit_code": code,
            "python": sys.version, "interpreter": sys.executable,
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "isolation": "temporary SQLite, R2/LLM/tracing disabled; no live services requested",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output, end="", flush=True)
        print("Baseline exit code:", code, flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
