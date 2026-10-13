"""Run one CSV benchmark using the configured MDM stage runner threshold.

Run this script in a fresh process for each threshold so cached settings cannot
leak between the two trials. The source files are read from masterone_test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from contextlib import ExitStack
from pathlib import Path

from masterone.auth import models as auth_models  # noqa: F401 - register user FK
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionPair, ResolutionRecord, ResolutionRun
from masterone.mdm.resolution_import import create_resolution_run_from_streams
from masterone.mdm.resolution_service import create_task, run_pipeline
from sqlalchemy import select
from sqlalchemy.orm import aliased

SOURCE_FILES = ("crm.csv", "erp.csv", "bill.csv")
REFERENCE_RUN = "150009e5-1918-49c5-83fb-7c26f06d7ef0"


def result_digest(db, run_id: str) -> tuple[int, str]:
    """Hash decisions by stable source/external IDs, independent of DB UUIDs."""
    left, right = aliased(ResolutionRecord), aliased(ResolutionRecord)
    rows = db.execute(
        select(left.source_system, left.external_id, right.source_system,
               right.external_id, ResolutionPair.pair_decision,
               ResolutionPair.triage_status, ResolutionPair.score)
        .select_from(ResolutionPair)
        .join(left, ResolutionPair.left_record_id == left.id)
        .join(right, ResolutionPair.right_record_id == right.id)
        .where(ResolutionPair.run_id == run_id)
    )
    normalized = []
    for source_a, external_a, source_b, external_b, decision, triage, score in rows:
        endpoints = sorted(((source_a, external_a), (source_b, external_b)))
        normalized.append(json.dumps((endpoints, decision, triage, round(score, 8)),
                                     separators=(",", ":")))
    digest = hashlib.sha256()
    for line in sorted(normalized):
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return len(normalized), digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=("stage_runner", "memory"))
    args = parser.parse_args()
    settings = get_settings()
    paths = [Path("masterone_test") / name for name in SOURCE_FILES]
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    engine, factory = create_database(settings.active_mdm_database_url)
    try:
        with factory() as db:
            reference = db.get(ResolutionRun, REFERENCE_RUN)
            if reference is None:
                raise RuntimeError("Reference run missing; cannot select benchmark owner")
            owner_user_id = reference.owner_user_id
        with ExitStack() as stack:
            sources = [(path.name, stack.enter_context(path.open("rb"))) for path in paths]
            with factory() as db:
                started = time.perf_counter()
                run, profile = create_resolution_run_from_streams(
                    db, sources, owner_user_id, f"Benchmark {args.label} 21k")
                import_s = time.perf_counter() - started
                run_id = run.id
        selected = ("stage_runner" if profile["row_count"] > settings.mdm_stage_runner_threshold
                    else "memory")
        if selected != args.label:
            raise RuntimeError(f"Threshold {settings.mdm_stage_runner_threshold} selects {selected}")
        create_task(run_id, factory)
        print(f"RUN_ID={run_id} LABEL={args.label} THRESHOLD={settings.mdm_stage_runner_threshold}", flush=True)
        started = time.perf_counter()
        run_pipeline(run_id, factory, lambda *_: None)
        pipeline_s = time.perf_counter() - started
        with factory() as db:
            run = db.get(ResolutionRun, run_id)
            pair_count, digest = result_digest(db, run_id)
            result = {
                "label": args.label, "run_id": run_id, "threshold": settings.mdm_stage_runner_threshold,
                "source_hashes": source_hashes, "record_count": run.record_count,
                "candidate_count": run.candidate_count, "insufficient_count": run.insufficient_count,
                "pair_count": pair_count, "result_digest": digest, "status": run.status,
                "import_s": round(import_s, 3), "pipeline_s": round(pipeline_s, 3),
            }
        output = Path("data/trace/benchmarks") / f"{args.label}-{run_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)
        print(f"BENCHMARK_FILE={output}", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
