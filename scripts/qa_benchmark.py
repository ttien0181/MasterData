"""Raw-only 10k stage benchmark; truth is accessed only after computation."""
import argparse
import csv
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

import psutil
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))


def benchmark(dataset, output, truth_root):
    from masterone.mdm.engine_adapter import EngineResultAdapter
    from masterone.mdm.pipeline.stage_runner import StageRunner
    output.mkdir(parents=True, exist_ok=False)
    records, key_by_id = [], {}
    for path in sorted(dataset.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                key = row["qa_record_key"]
                rid = hashlib.sha256(key.encode()).hexdigest()
                key_by_id[rid] = key
                raw = {k: v for k, v in row.items() if not k.startswith("qa_")}
                records.append({"id": rid, "external_id": row["source_record_id"],
                                "source_system": row["source_system"], "entity_type": row["entity_type"],
                                "source_updated_at": row["source_updated_at"], "raw_fields": raw})
    process, stop, peak = psutil.Process(), threading.Event(), [0]
    def sample():
        while not stop.wait(0.05):
            peak[0] = max(peak[0], process.memory_info().rss)
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    start, cpu_start = time.monotonic(), time.process_time()
    runner = StageRunner("qa-bench", "qa-attempt", output_dir=output)
    try:
        computed = runner.run_all(records)
        for pair in computed["pairs"]:
            EngineResultAdapter().pair(pair)
        measured = {"seconds": time.monotonic() - start, "cpu_seconds": time.process_time() - cpu_start,
                    "peak_rss_bytes": peak[0], "records": len(records),
                    "candidate_count": computed["candidate_count"], "serving_pairs": len(computed["pairs"]),
                    "proposals": len(computed["proposals"]), "review_cases": len(computed["review_cases"]),
                    "stages": [asdict(stage) for stage in runner.stage_results],
                    "candidate_metrics": computed["candidate_metrics"]}
        # Pin output invariants for correctness-preserving optimizations.
        measured["decision_hash"] = hashlib.sha256(json.dumps(computed["pairs"], sort_keys=True, default=str).encode()).hexdigest()
        measured["cluster_hash"] = hashlib.sha256(json.dumps(computed["proposals"], sort_keys=True, default=str).encode()).hexdigest()
        # Only this evaluation section may read truth. It cannot feed the pipeline.
        rows = pq.read_table(truth_root / dataset.name / "10k/seed-7/truth/records.parquet",
                             columns=["record_key", "entity_id", "label_status", "batch_id", "source_system"]).to_pylist()
        truth = {row["record_key"]: row for row in rows if row["label_status"] == "CERTAIN"}
        entities = defaultdict(list)
        for rid, key in key_by_id.items():
            if key in truth:
                entities[truth[key]["entity_id"]].append(rid)
        positives = {tuple(sorted(pair)) for ids in entities.values() for pair in combinations(ids, 2)}
        candidates = {tuple(sorted((row["left_id"], row["right_id"]))) for row in
                      pq.read_table(output / "qa-bench_candidates.parquet").to_pylist()}
        cross_batch = {pair for pair in positives if truth[key_by_id[pair[0]]]["batch_id"] != truth[key_by_id[pair[1]]]["batch_id"]}
        cross_source = {pair for pair in positives if truth[key_by_id[pair[0]]]["source_system"] != truth[key_by_id[pair[1]]]["source_system"]}
        merged = {tuple(sorted(pair)) for proposal in computed["proposals"]
                  for pair in combinations(proposal["member_record_ids"], 2)}
        labelled_merges = {pair for pair in merged if all(key_by_id[rid] in truth for rid in pair)}
        measured["quality"] = {"truth_used_during_pipeline": False, "label_status_counts": dict(Counter(row["label_status"] for row in rows)),
            "known_positive_pairs": len(positives), "candidate_true_pairs": len(candidates & positives),
            "candidate_recall": len(candidates & positives)/len(positives) if positives else None,
            "cross_batch_positive_pairs": len(cross_batch), "cross_batch_recovered": len(candidates & cross_batch),
            "cross_source_positive_pairs": len(cross_source), "cross_source_recovered": len(candidates & cross_source),
            "auto_merged_labelled_pairs": len(labelled_merges), "auto_merge_false_pairs": len(labelled_merges - positives),
            "auto_merge_recall": len(labelled_merges & positives)/len(positives) if positives else None,
            "comparison_reduction_ratio": 1-len(candidates)/(len(records)*(len(records)-1)/2)}
        (output / "measurement.json").write_text(json.dumps(measured, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"dataset": dataset.name, "status": "PASS_EXECUTION", **{k: measured[k] for k in
                ("seconds", "peak_rss_bytes", "candidate_count", "proposals", "quality")}}
    finally:
        stop.set()
        monitor.join()
        runner.close_decisions()
        runner.con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only")
    args = parser.parse_args()
    from run_merge_baseline import isolated_environment
    os.environ.update(isolated_environment(args.output / "environment"))
    results = []
    for dataset in sorted(args.datasets.glob("TC*")):
        if args.only and args.only != dataset.name:
            continue
        try:
            result = benchmark(dataset, args.output / dataset.name, ROOT.parent / "GenVN/benchmarks-v1")
        except Exception as error:
            result = {"dataset": dataset.name, "status": "FAIL", "error_type": type(error).__name__}
        results.append(result)
        (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
    return int(any(result["status"] == "FAIL" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
