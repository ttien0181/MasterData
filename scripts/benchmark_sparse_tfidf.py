"""Offline synthetic recall/RSS benchmark, each workload in a fresh process.

No production data, services, DB, or speed comparison. RSS includes interpreter,
imports, input records and pair union; OS peak counters include short allocations.
"""

import argparse
import ctypes
import gc
import json
import os
import random
import string
import subprocess
import sys
from itertools import combinations
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))


def peak_rss():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (key, ctypes.c_size_t) for key in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def fixture(size, kind):
    rng = random.Random(119)
    records, gold = [], set()
    width = 20 if kind == "duplicates" else 2
    for offset in range(0, size, width):
        token = "".join(rng.choices(string.ascii_lowercase, k=24))
        ids = []
        for j in range(min(width, size - offset)):
            rid = f"r{offset + j:06d}"
            name = token if kind == "duplicates" or j == 0 else token[:-1] + ("z" if token[-1] != "z" else "x")
            if offset == 0:
                name = "Nguyễn Văn An" if j == 0 else "Nguyen Van Anh"
            records.append({"id": rid, "normalized_fields": {"name": name,
                "dob": "1990-01-01" if j == 0 else "", "address_parts": {"province": "HN" if j == 0 else "HCM"}}})
            ids.append(rid)
        gold.update(combinations(ids, 2))
    return records, gold


def worker(size, kind):
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
    from masterone.mdm.pipeline.candidate_planner import WorkloadPlanError
    from masterone.mdm.pipeline.tfidf_blocking import GlobalTfidfIndex
    records, gold = fixture(size, kind)
    started = perf_counter()
    result = {"records": size, "distribution": kind, "gold_pairs": len(gold)}
    try:
        index = GlobalTfidfIndex(records)
        pairs = {tuple(sorted((a, b))) for a, b, _ in index.search()}
        result.update(tfidf_recall=len(gold & pairs) / len(gold), tfidf_pairs=len(pairs),
                      tfidf_seconds=perf_counter() - started, index_metrics=index.metrics)
        del index, pairs
        gc.collect()
        started_engine = perf_counter()
        candidates = CandidateEngine(CandidateConfig(sparse_tfidf=True, include_grams=False, composites=())).generate(records)
        result.update(engine_recall=len(gold & candidates.pairs.keys()) / len(gold),
                      engine_pairs=len(candidates.pairs), engine_seconds=perf_counter() - started_engine,
                      workload_plan=candidates.metrics["workload_plan"], status="completed")
    except WorkloadPlanError as error:
        result.update(status="guarded", rejected_plan=error.plan)
    result.update(total_seconds=perf_counter() - started, peak_rss_bytes=peak_rss(),
                  python=sys.version, platform=sys.platform)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--kind", choices=("unique", "duplicates"), default="unique")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "sparse-tfidf" / "benchmark.json")
    args = parser.parse_args()
    if args.worker:
        worker(args.size, args.kind)
        return
    reports = []
    for kind in ("unique", "duplicates"):
        for size in (1000, 5000, 10000):
            process = subprocess.run([sys.executable, "-X", "utf8", __file__, "--worker", "--size", str(size), "--kind", kind],
                                     capture_output=True, text=True, encoding="utf-8", check=True)
            report = json.loads(process.stdout)
            reports.append(report)
            print(json.dumps({k: report.get(k) for k in ("records", "distribution", "status", "tfidf_recall", "engine_recall", "peak_rss_bytes", "total_seconds")}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"fixture": "seed119: random 24-letter typo pairs, or 20-record entity groups; first group Vietnamese/cross-partition",
        "scope": "Synthetic only; peak OS RSS for fresh process including imports/input/index/engine union; no legacy speed comparison",
        "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
