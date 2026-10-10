"""Evaluate existing global sparse retrieval as a union, never use truth to retrieve."""
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from run_merge_baseline import isolated_environment

out = ROOT / "artifacts/full-system-20261009/global-candidate-probe"
out.mkdir(exist_ok=False)
os.environ.update(isolated_environment(out / "state"))
from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
from masterone.mdm.pipeline.tfidf_blocking import _partition_key

results = []
for name in ("TC3_hard_match", "TC5_real_world"):
    source = ROOT / "artifacts/full-system-20261009/stages-baseline" / name
    records = pq.read_table(source / "qa-bench_normalized_pass1.parquet").to_pylist()
    for record in records:
        if isinstance(record["normalized_fields"], str):
            record["normalized_fields"] = json.loads(record["normalized_fields"])
    baseline = {tuple(sorted((r["left_id"], r["right_id"]))) for r in
                pq.read_table(source / "qa-bench_candidates.parquet").to_pylist()}
    started = time.monotonic()
    item = {"dataset": name, "scope": "QA opt-in experiment, not default runtime change"}
    union = baseline
    try:
        generated = CandidateEngine(CandidateConfig(sparse_tfidf=True)).generate(records)
        union = baseline | set(generated.pairs)
        pq.write_table(pa.Table.from_pylist([{"left_id": a, "right_id": b} for a, b in sorted(union)]), out / (name + "_union.parquet"))
        item.update(status="PASS_EXECUTION", legacy_candidates_retained=len(baseline),
                    global_candidates=len(generated.pairs), union_candidates=len(union),
                    candidate_metrics=generated.metrics)
    except Exception as error:
        item.update(status="BLOCKED" if hasattr(error, "plan") else "FAIL", error_type=type(error).__name__,
                    plan=getattr(error, "plan", None))
    item["generation_seconds"] = time.monotonic() - started
    # Post-computation evaluation only; raw/normalized artifacts were created without truth.
    truth = pq.read_table(ROOT.parent / "GenVN/benchmarks-v1" / name / "10k/seed-7/truth/records.parquet",
                         columns=["record_key", "entity_id", "label_status"]).to_pylist()
    entities = defaultdict(list)
    for row in truth:
        if row["label_status"] == "CERTAIN":
            entities[row["entity_id"]].append(hashlib.sha256(row["record_key"].encode()).hexdigest())
    by_id = {r["id"]: r for r in records}
    positives = {tuple(sorted(pair)) for ids in entities.values() for pair in combinations(ids, 2)
                 if all(rid in by_id for rid in pair)}
    missed = positives - baseline
    partition_difference = sum(_partition_key(by_id[a]) != _partition_key(by_id[b]) for a, b in missed)
    evidence = Counter()
    for a, b in missed:
        left, right = by_id[a]["normalized_fields"], by_id[b]["normalized_fields"]
        for field in ("name", "phone", "email", "government_id", "dob"):
            if left.get(field) and left.get(field) == right.get(field):
                evidence["same_normalized_" + field] += 1
    item["quality"] = {"truth_used_during_retrieval": False, "known_positive_pairs": len(positives),
        "baseline_true_pairs": len(positives & baseline), "union_true_pairs": len(positives & union),
        "baseline_missed_different_partitions": partition_difference, "baseline_missing": len(missed),
        "missed_pair_equal_field_counts": dict(evidence)}
    results.append(item)
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in item.items() if k not in ("candidate_metrics", "plan")}), flush=True)
