"""Fresh TC1 E0-E3 diagnostic run through actual API/leased workflow jobs.

Controller enforces W1 over the complete benchmark process tree. No gold access,
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


def execute(out, experiment, fixed=False, keep_state=False):
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
            "ADVANCED_MATCHING_AGENTS_ENABLED": "false" if experiment in ("E0", "E1") else "true",
            "ADVANCED_MATCHING_INTEGRATION_ENABLED": "false" if experiment == "E0" else "true",
            "MDM_SCORE_WORKERS": "1",
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
    from masterone.mdm.matching_agents.integration_profile import integration_config
    from masterone.mdm.matching_foundation_models import MatchingProfile
    from masterone.mdm.matching_foundation_service import baseline_config
    from masterone.mdm.models import ResolutionRecord, ResolutionTask
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
    from masterone.mdm.pipeline.decision_semantics import person_constraints
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.scoring_engine import ScoringConfig, ScoringEngine, file_hash
    from masterone.mdm.pipeline.stage_runner import StageRunner
    from masterone.mdm.workflow_artifacts import iter_list
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
    if experiment == "E0":
        profile = checked(client.post(registry + "/profiles", json={"version": 1}))
    else:
        # Owned QA registry; production config builder accepts the already approved experiment identity.
        configuration = integration_config(baseline_config(), experiment)
        with factory.begin() as db:
            actor = db.query(User).filter_by(email_normalized=credentials["email"]).one().id
            row = MatchingProfile(owner_user_id=actor, creator_id=actor, data_scope="owner-private", version=1,
                config_schema_version=configuration["config_schema_version"], config_json=configuration,
                config_digest=__import__("masterone.mdm.generation_staging", fromlist=["digest"]).digest(configuration),
                score_unit=configuration["score_unit"], encoder_identity=configuration["encoder_identity"],
                feature_schema_identity=configuration["feature_schema_identity"])
            db.add(row)
            db.flush()
            identifier = row.id
        profile = checked(client.get(registry + "/profiles/" + identifier))
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
            "split": "LOCKED_TC1_SYNTHETIC_TEST_ONLY_NO_TUNING",
            "experiment": experiment, "fixed_universe": fixed,
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
    anchor = (ROOT / "artifacts/advanced-m2b-20261014/experiments-01/E0/source-identity-map.json"
              if fixed else out.parent / "E0/source-identity-map.json")
    if experiment != "E0" or fixed:
        # QA record identity reuse controls production top-K tie ordering across experiments.
        # Raw fields/normalization/labels are untouched; only the freshly imported QA PKs are remapped before PREPARE.
        from sqlalchemy import update
        if not anchor.is_file():
            raise ValueError("E0_IDENTITY_ANCHOR_REQUIRED")
        mapping = json.loads(anchor.read_text(encoding="utf-8"))
        inverted = {key: rid for rid, key in mapping.items()}
        batch_by_source = {spec["source_system"]: spec["batch_id"] for spec in catalog["files"]}
        with factory.begin() as db:
            imported = db.query(ResolutionRecord).filter_by(run_id=rid).all()
            keys = {f"{r.source_system}:{r.external_id}:{batch_by_source[r.source_system]}" for r in imported}
            if keys != set(inverted) or len(inverted) != len(mapping):
                raise ValueError("E0_IDENTITY_ANCHOR_UNIVERSE_MISMATCH")
            for row in imported:
                key = f"{row.source_system}:{row.external_id}:{batch_by_source[row.source_system]}"
                db.execute(update(ResolutionRecord).where(ResolutionRecord.id == row.id).values(id=inverted[key]),
                           execution_options={"synchronize_session": False})
        write(out / "identity-anchor.json", {"path": str(anchor.relative_to(ROOT)), "sha256": file_hash(anchor),
              "record_count": len(inverted), "scope": "QA_BEFORE_PREPARE_NO_RAW_OR_LABEL_CHANGES"})
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
    if fixed:
        import pyarrow.parquet as pq
        from masterone.mdm.pipeline.candidate_engine import CandidateResult
        with factory() as db:
            by_source = {(r.source_system, r.external_id): r.id for r in db.query(ResolutionRecord).filter_by(run_id=rid)}
        batch_by_source = {spec["source_system"]: spec["batch_id"] for spec in catalog["files"]}
        source_index = {f"{source}:{external}:{batch_by_source[source]}": internal for (source, external), internal in by_source.items()}
        endpoints = pq.read_table(dataset / "truth/answer_keys/evaluation_pairs.parquet", columns=["record_key_a", "record_key_b"]).to_pylist()
        forced = sorted({tuple(sorted((source_index[row["record_key_a"]], source_index[row["record_key_b"]]))) for row in endpoints})
        if any(a == b for a, b in forced) or len(forced) != len(endpoints):
            raise ValueError("FIXED_UNIVERSE_INVALID")
        def fixed_union(*args, **kwargs):
            return CandidateResult({key: set() for key in forced}, {"candidate_count": len(forced),
                "unique_candidates": len(forced), "record_count": len(rows), "retrieved_current": len(forced),
                "contract_version": "fixed_labeled_endpoint_universe_v1", "pass_order": ["fixed_universe"],
                "passes": {"fixed_universe": {"unique_pairs": len(forced), "new_unique": len(forced)}},
                "provenance": [{"pass": "fixed_universe", "left_id": a, "right_id": b} for a, b in forced]})
        # Research-only candidate injection; SCORE/assessment/agents/coordinator stay production implementations.
        CandidateEngine.generate = fixed_union
        import masterone.mdm.matching_agents.candidate_union as union_module
        union_module.build_union = fixed_union
        write(out / "fixed-universe.json", {"kind": "LABELED_ENDPOINTS_ONLY_NOT_FEATURES", "pairs": forced,
              "label_file_hash": file_hash(dataset / "truth/answer_keys/evaluation_pairs.parquet"), "label_values_accessed": False})
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
                    "agent_disagreement": result.get("agent_disagreement", False),
                    "triage_status": result.get("triage_status"),
                    "embedding_diagnostic": result.get("embedding_diagnostic"),
                    "blocking_reasons": row.baseline.get("blocking_reasons", []),
                }
            )
        artifact = db.query(WorkflowArtifact).filter_by(run_id=rid, phase="DECIDE").first()
        from masterone.mdm.workflow_artifacts import read_value
        decide = {key: read_value(db, artifact.id, rid, "DECIDE", [key])
                  for key in ("candidate_count", "candidate_metrics", "scoring_metrics")}
        # This ORM descriptor may hold the original full DECIDE JSON. It is not benchmark working state.
        db.expunge(artifact)
        del artifact
        evidence = state()["matching_agents"]
        if evidence:
            agent_artifact = db.get(WorkflowArtifact, evidence["artifact_id"])
            metadata = {k: v for k, v in agent_artifact.payload.items()
                        if k not in {"pairs", "full_snapshot_contact_frequencies", "__workflow_parts__"}}
            total_agents = sum(1 for _ in iter_list(db, agent_artifact.id, rid, "MATCHING_AGENTS", ["pairs"]))
        else:
            metadata, total_agents = None, 0
        batch_by_key = {(r["source_system"], r["source_record_id"]): spec["batch_id"]
                        for spec in catalog["files"]
                        for r in rows if r["source_system"] == spec["source_system"]}
        source_keys = {r.id: f"{r.source_system}:{r.external_id}:{batch_by_key[r.source_system, r.external_id]}"
                       for r in db.query(ResolutionRecord).filter_by(run_id=rid)}
        if any(set(r.extra_fields or {}) - {"source_updated_at", "is_active"} or r.benchmark_entity_id is not None
               for r in db.query(ResolutionRecord).filter_by(run_id=rid)):
            raise ValueError("TRUTH_OR_EXTRA_FIELD_LEAKAGE")
    write(out / "source-identity-map.json", source_keys)
    write(out / "predictions-pre-hitl.json", pairs)
    import gc
    gc.collect()
    if fixed:
        groups, golden = [], []
    else:
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
    hard_checks = [person_constraints(records[p["left_id"]], records[p["right_id"]]) for p in pairs]
    violations = sum(check.forbids_merge for p, check in zip(pairs, hard_checks) if p["decision"] == "MERGEABLE")
    report = {
        "experiment": experiment, "fixed_universe": fixed,
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
        "cluster_result": None if fixed else state()["cluster_result"],
        "golden_drafts": len(golden),
        "cluster_count": len(groups),
        "hard_constraint_matches": violations,
        "guard_checks": len(hard_checks), "detected_hard_conflicts": sum(check.forbids_merge for check in hard_checks),
        "agent_disagreement_pairs": sum(p["agent_disagreement"] for p in pairs),
        "review_pairs": sum(p["decision"] == "REVIEW" for p in pairs),
        "required_review_pairs": state()["counts"]["review_required"]["value"],
        "workflow_artifact_bytes": artifact_bytes,
        "artifact_phases": dict(phase_counts),
        "quality_metrics": None,
        "labels_accessed": False,
        "auto_merge_enabled": False,
        "post_hitl": "NOT_RUN",
        "source_hashes": sources,
    }
    if (evidence and (evidence["status"] != "OK" or total_agents != len(pairs))) or violations:
        raise ValueError("TRIAL_ACCEPTANCE_FAILED")
    if any(file_hash(ROOT / path) != pin for path, pin in {**sources, **code_pins}.items()):
        raise ValueError("SOURCE_CHANGED_DURING_TRIAL")
    write(out / "report.json", report)
    # Predictions/config are frozen before opening label VALUES. Endpoint-only reading is explicit in fixed scoring track.
    import pyarrow.parquet as pq
    sys.path.insert(0, str(ROOT / "benchmark"))
    from m2b_evaluation import evaluate_diagnostics
    from masterone.mdm.generation_staging import digest
    from standalone.contracts.labels import PairLabel
    label_path = dataset / "truth/answer_keys/evaluation_pairs.parquet"
    if file_hash(label_path) != manifest["file_hashes"]["truth/answer_keys/evaluation_pairs.parquet"]:
        raise ValueError("LABEL_MANIFEST_MISMATCH")
    labels, label_rows = {}, {}
    for row in pq.read_table(label_path).to_pylist():
        key = digest(sorted((row["record_key_a"], row["record_key_b"])))
        if key in labels or row["record_key_a"] == row["record_key_b"]:
            raise ValueError("DUPLICATE_OR_SELF_LABEL_PAIR")
        labels[key] = {"MATCH": PairLabel.POSITIVE, "NON_MATCH": PairLabel.NEGATIVE, "UNKNOWN": PairLabel.UNKNOWN}[row["label"]]
        label_rows[key] = row
    predictions, routing, candidates = {}, {}, set()
    for pair in pairs:
        key = digest(sorted((source_keys[pair["left_id"]], source_keys[pair["right_id"]])))
        candidates.add(key)
        if experiment == "E2":
            prediction = pair["embedding_diagnostic"]["guarded_decision"]
        else:
            prediction = "MATCH" if pair["decision"] == "MERGEABLE" and pair["eligibility"]["manual_allowed"] else pair["decision"]
        predictions[key] = prediction
        routing[key] = "MATCH" if pair["decision"] == "MERGEABLE" and pair["eligibility"]["auto_allowed"] else pair["decision"]
    pass_ids = {}
    order = decide["candidate_metrics"].get("pass_order", [])
    if decide["candidate_metrics"].get("provenance_artifact_id"):
        with factory() as db:
            for observation in iter_list(db, decide["candidate_metrics"]["provenance_artifact_id"], rid, "CANDIDATE_PROVENANCE", ["observations"]):
                key = digest(sorted((source_keys[observation["left_id"]], source_keys[observation["right_id"]])))
                pass_ids.setdefault(observation["pass"], set()).add(key)
    elif fixed:
        pass_ids = {"fixed_universe": candidates}
    else:
        # Legacy published pair blocking reasons are the baseline source of pass attribution.
        for row in pairs:
            key = digest(sorted((source_keys[row["left_id"]], source_keys[row["right_id"]])))
            for name in row.get("blocking_reasons", []):
                pass_ids.setdefault(name, set()).add(key)
        order = sorted(pass_ids)
    quality = evaluate_diagnostics(labels, predictions, candidates, pass_ids, order, len(records))
    quality["final_routing"] = evaluate_diagnostics(labels, routing, candidates, pass_ids, order, len(records))["classification"]
    quality.update(label_hash=file_hash(label_path), prediction_hash=file_hash(out / "predictions-pre-hitl.json"),
                   independent_gold=False, no_tuning=True, fixed_universe=fixed)
    if fixed:
        quality.update(candidate_recall=None, reduction_ratio=None, candidate_recall_reason="FIXED_UNIVERSE_SCORING_NOT_RETRIEVAL",
                       reduction_ratio_reason="FIXED_UNIVERSE_SCORING_NOT_RETRIEVAL")
    write(out / "evaluation.json", quality)
    write(out / "labeled-errors.json", [{"pair_key": key, "source_left": row["record_key_a"], "source_right": row["record_key_b"],
          "gold": row["label"], "prediction": predictions.get(key, "BLOCKING_MISS"),
          "candidate": key in candidates, "relation_type": row["relation_type"], "difficulty": row["difficulty"]}
          for key, row in label_rows.items() if row["label"] != "UNKNOWN" and
          ((row["label"] == "MATCH") != (predictions.get(key) == "MATCH"))])
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


def controller(out, experiment, fixed=False, keep_state=False):
    import psutil

    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--execute", "--output", str(out), "--experiment", experiment] + (["--fixed-universe"] if fixed else [])
    if keep_state:
        command.append("--keep-state")
    write(out / "command.json", command)
    (out / "executed-runner.py").write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    started, peak, violation = time.monotonic(), 0, None
    last_disk_check, output_bytes = 0., 0
    with (out / "pipeline.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        process = psutil.Process(child.pid)
        try:
            while child.poll() is None:
                try:
                    peak = max(peak, sum(p.memory_info().rss for p in [process] + process.children(recursive=True)))
                    if time.monotonic() - last_disk_check > 2:
                        output_bytes = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
                        last_disk_check = time.monotonic()
                    if output_bytes > 12 * 1024**3:
                        violation = "WORKER_DISK_BUDGET"
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
    if code:
        write(out / "failure.json", {"status": "ERROR", "exit_code": code, "budget_violation": violation, "metrics": None, "no_partial_success": True})
    write(
        out / "supervisor.json",
        {
            "exit_code": code,
            "violation": violation,
            "peak_process_tree_rss_bytes": peak,
            "whole_trial_seconds": time.monotonic() - started,
            "sampling_seconds": 0.05,
            "cap_bytes": 8 * 1024**3,
            "output_bytes": output_bytes, "disk_cap_bytes": 12 * 1024**3,
        },
    )
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--experiment", choices=("E0", "E1", "E2", "E3", "all"), required=True)
    parser.add_argument("--fixed-universe", action="store_true")
    parser.add_argument("--keep-state", action="store_true", help="Retain sandbox SQLite for debugging/resume")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / "artifacts/advanced-m2b-20261014").resolve()):
        raise ValueError("OWNED_QA_OUTPUT_REQUIRED")
    if args.execute:
        execute(out, args.experiment, args.fixed_universe, keep_state=args.keep_state)
    elif args.experiment == "all":
        out.mkdir(parents=True, exist_ok=False)
        outcomes = {}
        for experiment in (("E0", "E2", "E3") if args.fixed_universe else ("E0", "E1", "E2", "E3")):
            outcomes[experiment] = controller(out / experiment, experiment, args.fixed_universe, keep_state=args.keep_state)
        write(out / "outcomes.json", outcomes)
        raise SystemExit(0 if not any(outcomes.values()) else 1)
    else:
        raise SystemExit(controller(out, args.experiment, args.fixed_universe, keep_state=args.keep_state))
