"""Fresh TC1 source-only performance run through actual API/leased workflow jobs.

Controller enforces B1 over the complete benchmark process tree. No gold access,
training, thresholds, production database or Docker configuration.
"""

import argparse
import csv
import functools
import io
import json
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from artifact_retention import finish_run
from run_merge_baseline import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def execute(out, keep_state=False):
    import yaml
    from qa_dataset_adapter import FIELDS, decoded_rows

    env = isolated_environment(out / "state")
    env.update(
        {
            "MASTERONE_ENV_FILE": str(out / "absent.env"),
            "SESSION_WORKFLOW_ENABLED": "true",
            "PAIR_ASSESSMENT_AUTO_ENABLED": "true",
            "WORKFLOW_PARQUET_ENABLED": "false",
            "PAIR_AUTOMATION_ENABLED": "false",
            "PAIR_REVIEW_MODEL_PATH": "",
            "LLM_PAIR_ASSESSMENT_ENABLED": "false",
            "MATCHING_PROFILE_REGISTRY_ENABLED": "true",
            "ADVANCED_MATCHING_AGENTS_ENABLED": "true",
            "EMBEDDING_RUNTIME_ROOT": str(ROOT / "artifacts/advanced-m2a-20261014/e5-trial-01"),
            "WORKFLOW_ARTIFACT_DIR": str(out / "state/workflow-artifacts"),
        }
    )
    os.environ.update(env)
    sys.path[:0] = [str(ROOT / "backend/src"), str(ROOT / "backend")]
    from fastapi.testclient import TestClient
    from masterone.auth.models import User
    from masterone.config import get_settings
    from masterone.db.base import Base
    from masterone.db.session import get_session
    from masterone.main import app
    from masterone.mdm.job_queue import claim_next, heartbeat, ownership
    from masterone.mdm.job_queue import progress as job_progress
    from masterone.mdm.models import ResolutionRecord, ResolutionTask
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.hard_constraints import check_all
    from masterone.mdm.pipeline.scoring_engine import ScoringConfig, ScoringEngine, file_hash
    from masterone.mdm.pipeline.stage_runner import StageRunner
    from masterone.mdm.workflow_artifacts import iter_list, load
    from masterone.mdm.workflow_assessment_reads import effective_result
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowPair
    from masterone.mdm.workflow_review import eligibility
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    dataset = ROOT / "artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7"
    manifest = yaml.safe_load((dataset / "manifest.yaml").read_text(encoding="utf-8"))
    catalog_path = dataset / "raw/source_mapping.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    sources, rows = {}, []
    for spec in catalog["files"]:
        path = (dataset / spec["path"]).resolve()
        if (
            not path.is_relative_to((dataset / "raw").resolve())
            or file_hash(path) != manifest["file_hashes"][spec["path"]]
        ):
            raise ValueError("SOURCE_MANIFEST_MISMATCH")
        sources[str(path.relative_to(ROOT))] = file_hash(path)
        iterator = iter(decoded_rows(path, spec))
        for _ in range(spec["header_row"]):
            next(iterator)
        header = next(iterator)
        for values in iterator:
            original = dict(zip(header, values, strict=True))
            rows.append({field: original[spec["columns"][field]] for field in FIELDS})
    if len(rows) != 10022 or len({(r["source_system"], r["source_record_id"]) for r in rows}) != len(rows):
        raise ValueError("TC1_SOURCE_COUNT_OR_ID")
    sources[str(catalog_path.relative_to(ROOT))] = file_hash(catalog_path)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (out / "source-only-input.csv").write_text(text.getvalue(), encoding="utf-8")
    settings = get_settings()
    if not settings.active_mdm_database_url.startswith("sqlite:///") or settings.r2_enabled or settings.llm_enabled:
        raise ValueError("ISOLATED_EXECUTION_REQUIRED")
    code_pins = {str(p.relative_to(ROOT)): file_hash(p) for p in (ROOT / "backend/src/masterone").rglob("*.py")}
    engine = create_engine(settings.active_mdm_database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    def session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.state.session_factory = factory
    client = TestClient(app)
    client.headers["Origin"] = "http://localhost:3000"

    def checked(response):
        if response.status_code not in (200, 201, 202):
            raise ValueError(f"QA_API_{response.status_code}")
        return response.json()

    credentials = {"email": "m2-owned-trial@example.com", "password": "m2-isolated-test-password"}
    checked(client.post("/api/v1/auth/register", json=credentials))
    with factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials["email"]).one().role = "STEWARD"
    client.headers["X-CSRF-Token"] = checked(client.post("/api/v1/auth/login", json=credentials))["csrf_token"]
    registry = "/api/v1/mdm/matching"
    profile = checked(
        client.post(
            registry + "/profiles", json={"version": 1, "advanced_agents": True, "fallback_mode": "fail_operation"}
        )
    )
    checked(client.post(registry + f"/profiles/{profile['id']}/validate", json={"expected_revision": 1}))
    checked(
        client.put(registry + "/defaults/owner-private", json={"profile_id": profile["id"], "expected_revision": 0})
    )
    write(
        out / "config.json",
        {
            "profile": profile,
            "candidate": asdict(CandidateConfig()),
            "scoring": asdict(ScoringConfig()),
            "flags": {key: getattr(settings, key) for key in type(settings).model_fields if key.endswith("_enabled")},
            "source_hashes": sources,
            "code_hashes": code_pins,
            "labels_accessed": False,
            "split": "NONE; performance trial only",
        },
    )
    stages, nested = [], []

    def timed(name, function):
        print("START", name, flush=True)
        start = time.perf_counter()
        value = function()
        stages.append({"stage": name, "seconds": time.perf_counter() - start})
        print("DONE", name, stages[-1]["seconds"], flush=True)
        return value

    def instrument(owner, name, label):
        original = getattr(owner, name)

        @functools.wraps(original)
        def measured(*args, **kw):
            start = time.perf_counter()
            try:
                return original(*args, **kw)
            finally:
                nested.append({"stage": label, "seconds": time.perf_counter() - start})

        setattr(owner, name, measured)

    instrument(CandidateEngine, "generate", "candidate-generation")
    instrument(ScoringEngine, "write_scores", "rule-scoring")
    import masterone.mdm.workflow_assessment as assessment_module

    instrument(assessment_module, "compute_assessment", "rule-assessment")
    original_init = StageRunner.__init__

    def retained_init(self, run_id, attempt_id, output_dir=None, db_url=None):
        return original_init(self, run_id, attempt_id, output_dir=out / "stages" / attempt_id, db_url=db_url)

    StageRunner.__init__ = retained_init

    def worker():
        claim = claim_next(factory, "m2-source-only-qa", lease_seconds=300, timeout_seconds=1800)
        if not claim:
            raise ValueError("QA_EXPECTED_CLAIM")
        with factory() as db:
            op_id = db.get(ResolutionTask, claim.task_id).operation_id
        last = [time.monotonic()]

        def report(phase, done, total):
            if time.monotonic() - last[0] > 30:
                heartbeat(factory, claim, 300)
                last[0] = time.monotonic()
            job_progress(factory, claim, phase, done, total)

        with ownership(claim):
            execute_workflow(op_id, factory, report)

    start = time.perf_counter()
    with (out / "source-only-input.csv").open("rb") as stream:
        rid = timed(
            "import-api",
            lambda: checked(
                client.post(
                    "/api/v1/mdm/runs",
                    data={"workflow_version": "2"},
                    files={"files": ("tc1-source-only.csv", stream, "text/csv")},
                )
            ),
        )["run_id"]
    root = f"/api/v1/mdm/runs/{rid}/workflow"

    def state():
        return checked(client.get(root))

    serial = [0]

    def mutation(**extra):
        serial[0] += 1
        current = state()
        return {
            "request_id": f"m2-{serial[0]}",
            "expected_workflow_revision": current["workflow_revision"],
            "expected_assessment_id": current["assessment_id"],
            **extra,
        }

    timed("prepare-worker", worker)
    timed("enqueue-decide-api", lambda: checked(client.post(root + "/start-decide", json=mutation())))
    timed("decide-rule-assessment-agents-worker", worker)
    with factory() as db:
        if db.query(ResolutionRecord).filter_by(run_id=rid).count() != 10022:
            raise ValueError("RECORD_LOSS")
        records = {r.id: record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=rid)}
        if any(r.get("benchmark_entity_id") for r in records.values()):
            raise ValueError("LABEL_LEAKAGE")
        pairs = []
        for row in db.query(WorkflowPair).filter_by(run_id=rid).order_by(WorkflowPair.id):
            result = effective_result(row)
            pairs.append(
                {
                    "pair_id": row.id,
                    "left_id": result["left_id"],
                    "right_id": result["right_id"],
                    "score": result["score"],
                    "decision": result["decision"],
                    "group": result.get("primary_group"),
                    "overlay": row.decision,
                    "eligibility": eligibility(row),
                }
            )
        artifact = db.query(WorkflowArtifact).filter_by(run_id=rid, phase="DECIDE").first()
        decide = load(db, artifact.id, rid, "DECIDE")
        evidence = state()["matching_agents"]
        agent_artifact = db.get(WorkflowArtifact, evidence["artifact_id"])
        metadata = {
            k: v
            for k, v in agent_artifact.payload.items()
            if k not in {"pairs", "full_snapshot_contact_frequencies", "__workflow_parts__"}
        }
        total_agents = sum(1 for _ in iter_list(db, agent_artifact.id, rid, "MATCHING_AGENTS", ["pairs"]))
    write(out / "predictions-pre-hitl.json", pairs)
    timed(
        "enqueue-preview-api",
        lambda: checked(
            client.post(
                root + "/cluster-preview",
                json=mutation(expected_decision_revision=state()["decision_revision"], allow_partial=True),
            )
        ),
    )
    timed("cluster-preview-worker", worker)
    timed(
        "enqueue-cluster-api",
        lambda: checked(
            client.post(
                root + "/finalize-decisions",
                json=mutation(expected_decision_revision=state()["decision_revision"], allow_partial=True),
            )
        ),
    )
    timed("cluster-publication-worker", worker)

    def pages(path):
        values, cursor = [], None
        while True:
            page = checked(client.get(root + path, params={"limit": 100, **({"cursor": cursor} if cursor else {})}))
            values.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return values

    groups = timed("cluster-reads-api", lambda: pages("/clusters"))
    golden = timed("golden-draft-reads-api", lambda: pages("/golden"))
    elapsed = time.perf_counter() - start
    with factory() as db:
        phase_counts = Counter()
        artifact_bytes = 0
        for artifact in db.query(WorkflowArtifact).filter_by(run_id=rid).yield_per(50):
            phase_counts[artifact.phase] += 1
            artifact_bytes += len(json.dumps(artifact.payload, ensure_ascii=False).encode())
    violations = sum(
        check_all(records[p["left_id"]], records[p["right_id"]], frozenset()) is not None
        for p in pairs
        if p["decision"] == "MERGEABLE"
    )
    report = {
        "experiment": "M2A_CPU_EVIDENCE_TRIAL",
        "run_id": rid,
        "records": 10022,
        "candidate_pairs": decide["candidate_count"],
        "unique_pairs": len({(p["left_id"], p["right_id"]) for p in pairs}),
        "groups": dict(Counter(p["group"] for p in pairs)),
        "decisions": dict(Counter(p["decision"] for p in pairs)),
        "overlays": dict(Counter(p["overlay"] for p in pairs)),
        "agent_pairs": total_agents,
        "matching_agents": evidence,
        "agent_metadata": metadata,
        "candidate_metrics": decide["candidate_metrics"],
        "scoring_metrics": decide["scoring_metrics"],
        "stages": stages,
        "nested_stages": nested,
        "pipeline_seconds": elapsed,
        "cluster_result": state()["cluster_result"],
        "golden_drafts": len(golden),
        "cluster_count": len(groups),
        "hard_constraint_matches": violations,
        "workflow_artifact_bytes": artifact_bytes,
        "artifact_phases": dict(phase_counts),
        "quality_metrics": None,
        "labels_accessed": False,
        "auto_merge_enabled": False,
        "post_hitl": "NOT_RUN",
        "source_hashes": sources,
    }
    if evidence["status"] != "OK" or total_agents != len(pairs) or violations:
        raise ValueError("TRIAL_ACCEPTANCE_FAILED")
    if any(file_hash(ROOT / path) != pin for path, pin in {**sources, **code_pins}.items()):
        raise ValueError("SOURCE_CHANGED_DURING_TRIAL")
    write(out / "report.json", report)
    engine.dispose()
    finish_run(out, keep_state=keep_state)
    write(
        out / "manifest.json",
        {
            "files": [
                {"path": str(p.relative_to(out)), "bytes": p.stat().st_size, "sha256": file_hash(p)}
                for p in out.rglob("*")
                if p.is_file() and p.name != "manifest.json"
            ]
        },
    )


def controller(out, keep_state=False):
    import psutil

    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--execute", "--output", str(out)]
    if keep_state:
        command.append("--keep-state")
    write(out / "command.json", command)
    started, peak, violation = time.monotonic(), 0, None
    with (out / "pipeline.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        process = psutil.Process(child.pid)
        try:
            while child.poll() is None:
                try:
                    peak = max(peak, sum(p.memory_info().rss for p in [process] + process.children(recursive=True)))
                    if peak > 8 * 1024**3:
                        violation = "WORKER_RSS_BUDGET"
                    if time.monotonic() - started > 1800:
                        violation = "WORKER_TIMEOUT"
                    if violation:
                        break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(0.05)
        finally:
            if child.poll() is None:
                for descendant in process.children(recursive=True):
                    try:
                        descendant.kill()
                    except psutil.NoSuchProcess:
                        pass
                child.kill()
            code = child.wait()
    write(
        out / "supervisor.json",
        {
            "exit_code": code,
            "violation": violation,
            "peak_process_tree_rss_bytes": peak,
            "whole_trial_seconds": time.monotonic() - started,
            "sampling_seconds": 0.05,
            "cap_bytes": 8 * 1024**3,
        },
    )
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--keep-state", action="store_true", help="Retain sandbox SQLite for debugging/resume")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / "artifacts/advanced-m2a-20261014").resolve()):
        raise ValueError("OWNED_QA_OUTPUT_REQUIRED")
    if args.execute:
        execute(out, keep_state=args.keep_state)
    else:
        raise SystemExit(controller(out, keep_state=args.keep_state))
