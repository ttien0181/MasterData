"""Offline, pinned migration shadow comparison. Never connects to production.

Run with the repository's Python environment. Evidence and downloaded data stay
under ignored artifacts/merge-validation. Labels never enter matching inputs.
The public restaurant set is an out-of-domain stress check, not PERSON approval.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "937bfa9dc314cb4e64ee590ee05aae6aa5e618e4"
PUBLIC = "c39fcae0f59d79f6842da3c6cb07c2a87bf1b55b"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
        sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def pair_key(a, b):
    return tuple(sorted((a, b)))


def ratio(a, b):
    return a / b if b else None


def labeled_metrics(candidates, predictions, labels, clusters):
    """Unlabeled predictions are unknown, never silently false positives."""
    positive = {k for k, v in labels.items() if v}
    negative = set(labels) - positive
    tp, fp = len(predictions & positive), len(predictions & negative)
    fn, tn = len(positive - predictions), len(negative - predictions)
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)
    violations = []
    for members in clusters:
        members = set(members)
        violations.append(sum(a in members and b in members for a, b in negative))
    return {"labeled_pairs": len(labels), "positive_labels": len(positive),
        "negative_labels": len(negative), "candidate_recall": ratio(len(candidates & positive), len(positive)),
        "candidate_positive_hits": len(candidates & positive), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "predicted_pairs": len(predictions), "unknown_predictions": len(predictions - set(labels)),
        "clusters_violating_known_negatives": sum(v > 0 for v in violations),
        "known_negative_cluster_pairs": sum(violations),
        "calibration": "NOT_TESTED: rule scores are not probabilities; no evaluated probability model"}


def canonical_business(result):
    # Sort containers only. Preserve IDs, evidence, golden provenance and every
    # business value so a transport comparison cannot hide a semantic change.
    return {key: sorted(list(result[key]), key=lambda r: r["id"])
            for key in ("pairs", "proposals", "review_cases")}


def prepare(directory, reference):
    directory.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "archive", "--format=zip", OLD, "backend/src"], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for name in z.namelist():
            if not (directory / "old" / name).resolve().is_relative_to((directory / "old").resolve()):
                raise ValueError("Unsafe archive path")
        z.extractall(directory / "old")
    provenance = {"p119_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "old_head": OLD, "public_revision": PUBLIC, "created_at": datetime.now(UTC).isoformat(),
        "public_domain": "restaurants; UNKNOWN compatibility stress, not PERSON ground truth",
        "internal_domain": "team-generated PERSON sample; 1200 labeled pairs, incomplete gold",
        "environment": {"python": sys.version, "platform": sys.platform}, "sources": {}}
    provenance["p119_runtime_hashes"] = {p.relative_to(ROOT).as_posix(): file_hash(p)
        for p in sorted((ROOT / "backend/src/masterone").rglob("*"))
        if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".json"} and "__pycache__" not in p.parts}
    if reference and reference.is_dir():
        provenance["onemaster_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=reference, text=True).strip()
        provenance["onemaster_status"] = subprocess.check_output(["git", "status", "--porcelain"], cwd=reference, text=True).splitlines()
        paths = [p for folder in ("src", "backend", "tests", "configs") for p in (reference / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".toml"} and "__pycache__" not in p.parts]
        provenance["onemaster_source_hashes"] = {p.relative_to(reference).as_posix(): file_hash(p) for p in sorted(paths)}
    source = directory / "datasets"
    source.mkdir()
    for name in ("fodors.csv", "zagats.csv", "fodors-zagats_perfectMapping.csv"):
        url = f"https://raw.githubusercontent.com/qcri/deeper-lite/{PUBLIC}/Lua/data/fodors-zagats/{name}"
        data = urllib.request.urlopen(url, timeout=60).read()
        (source / name).write_bytes(data)
        provenance["sources"][name] = {"url": url, "sha256": file_hash(source / name)}
    sys.path.insert(0, str(ROOT / "backend/src"))
    from masterone.mdm.pipeline.standardization import standardize
    for dataset in ("public", "internal"):
        records, labels = [], {}
        if dataset == "public":
            for name, prefix in (("fodors.csv", "f"), ("zagats.csv", "z")):
                with (source / name).open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        def clean(value):
                            return value.strip("'").replace("\\'", "'")
                        rid = prefix + row["id"]
                        raw = {"name": clean(row["name"]), "address": clean(row["addr"]) + ", " + clean(row["city"]),
                            "phone": row["phone"], "email": ""}
                        normalized, flags = standardize(raw)
                        records.append({"id": rid, "external_id": rid,
                            "source_system": "CRM" if prefix == "f" else "ERP", "entity_type": "UNKNOWN",
                            "raw_fields": raw, "normalized_fields": normalized, "quality_flags": flags})
            with (source / "fodors-zagats_perfectMapping.csv").open(encoding="utf-8-sig", newline="") as f:
                gold = {pair_key("f" + row["fodors_id"], "z" + row["zagats_id"]) for row in csv.DictReader(f)}
            # Closed-world cross-table perfectMapping protocol only; do not infer
            # within-table negative labels or a complete PERSON entity partition.
            left = [r["id"] for r in records if r["id"].startswith("f")]
            right = [r["id"] for r in records if r["id"].startswith("z")]
            labels = {pair_key(a, b): int(pair_key(a, b) in gold) for a in left for b in right}
        else:
            for name in ("records.csv", "pairs.csv"):
                path = ROOT / "masterone_test" / name
                provenance["sources"]["internal/" + name] = {"sha256": file_hash(path), "path": str(path.relative_to(ROOT))}
            with (ROOT / "masterone_test/records.csv").open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    raw = {"name": row["full_name"], "phone": row["phone_number"],
                        "email": row["email_address"], "address": row["raw_address"]}
                    normalized, flags = standardize(raw)
                    records.append({"id": row["id"], "external_id": row["id"],
                        "source_system": row["source_system"], "entity_type": "PERSON",
                        "raw_fields": raw, "normalized_fields": normalized, "quality_flags": flags})
            with (ROOT / "masterone_test/pairs.csv").open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    key, value = pair_key(row["id_a"], row["id_b"]), int(row["is_match"])
                    if key in labels and labels[key] != value:
                        raise ValueError("Conflicting labels")
                    labels[key] = value
        ids = {r["id"] for r in records}
        if len(ids) != len(records) or any(a not in ids or b not in ids for a, b in labels):
            raise ValueError("Invalid label/input coverage")
        records.sort(key=lambda r: r["id"])
        write(directory / (dataset + "-snapshot.json"), records)
        write(directory / (dataset + "-labels.json"), [[a, b, v] for (a, b), v in sorted(labels.items())])
        provenance[dataset] = {"records": len(records), "labels": len(labels), "positive": sum(labels.values()),
            "snapshot_sha256": file_hash(directory / (dataset + "-snapshot.json")), "labels_sha256": file_hash(directory / (dataset + "-labels.json"))}
    write(directory / "provenance.json", provenance)


def child(directory, dataset, profile, batch, partition, workers, reverse):
    # A fresh process pins imports to exactly one engine revision.
    sys.path.insert(0, str((directory / "old/backend/src") if profile == "old" else ROOT / "backend/src"))
    records = json.loads((directory / (dataset + "-snapshot.json")).read_text(encoding="utf-8"))
    from masterone.mdm import resolution_service as service
    name = f"{dataset}-{profile}-b{batch}-p{partition}-w{workers}" + ("-reverse" if reverse else "")
    work = directory / name
    work.mkdir(exist_ok=False)
    events, candidates, queues, uploaders = [], {}, [], []
    metrics = {"upload_seconds_sum": 0., "upload_bytes": 0, "backpressure_wait_seconds_sum": 0.}
    lock = threading.Lock()
    from masterone.config import get_settings
    settings = get_settings()
    kwargs = {}
    if profile == "old":
        original = service.generate_candidates
        def capture(rows):
            result = original(rows)
            candidates.update(result)
            return result
        service.generate_candidates = capture
    else:
        from masterone.mdm.engine_adapter import CORE_FEATURES, STREAMING_FEATURES, engine_context
        from masterone.mdm.pipeline.candidate_engine import CandidateEngine
        from masterone.mdm.pipeline.scoring_engine import ScoringConfig
        from masterone.mdm.pipeline.streaming import ReferenceExchange
        from masterone.storage.background_upload import BackgroundUploader
        from masterone.storage.object_store import LocalObjectStore
        original = CandidateEngine.finalize
        def capture(self):
            result = original(self)
            candidates.update(result.pairs)
            return result
        CandidateEngine.finalize = capture
        settings.stream_partition_rows = partition
        settings.stream_partition_bytes = 16 * 1024**2
        settings.stream_queue_bytes = 64 * 1024**2
        settings.stream_queue_partitions = 4
        settings.stream_consumer_workers = workers
        settings.stream_upload_workers = workers
        kwargs = {"scoring_config": ScoringConfig(batch_size=batch), "scoring_output_dir": work / "compute"}
        config = {k: profile.startswith("v2") for k in CORE_FEATURES}
        config.update({k: profile.endswith("stream") for k in STREAMING_FEATURES})
        # Instrument actual waits, not total producer/serialization latency.
        def observe_init(cls, target):
            init = cls.__init__
            def wrapper(self, *a, **kw):
                init(self, *a, **kw)
                target.append(self)
                wait = self.condition.wait
                def timed_wait(*args, **kwargs):
                    start = time.perf_counter()
                    admission = sys._getframe(1).f_code.co_name in {"submit", "_reserve"}
                    try:
                        return wait(*args, **kwargs)
                    finally:
                        if admission:
                            with lock:
                                metrics["backpressure_wait_seconds_sum"] += time.perf_counter() - start
                self.condition.wait = timed_wait
            cls.__init__ = wrapper
        observe_init(ReferenceExchange, queues)
        observe_init(BackgroundUploader, uploaders)
        class TimedStore(LocalObjectStore):
            def upload_file(self, path, key, content_type="application/octet-stream"):
                start = time.perf_counter()
                try:
                    return super().upload_file(path, key, content_type)
                finally:
                    with lock:
                        metrics["upload_seconds_sum"] += time.perf_counter() - start
                        metrics["upload_bytes"] += Path(path).stat().st_size
            def upload_bytes(self, data, key, content_type="application/octet-stream"):
                start = time.perf_counter()
                try:
                    return super().upload_bytes(data, key, content_type)
                finally:
                    with lock:
                        metrics["upload_seconds_sum"] += time.perf_counter() - start
                        metrics["upload_bytes"] += len(data)
        kwargs["_stream_store"] = TimedStore(work / "remote")
        gates = {k: threading.Event() for k in ("normalize", "score", "decide")}
        def event(stage, part=None):
            if reverse and part == "p00000000":
                key = {"normalize:pass_one": "normalize", "score:batch": "score", "decide:pair_local": "decide"}.get(stage)
                if key and not gates[key].wait(30):
                    raise RuntimeError("Cannot prove reversed completion")
            with lock:
                events.append([stage, part, time.perf_counter()])
            if part == "p00000001":
                key = {"normalized:local": "normalize", "scores:local": "score", "decision-prep:local": "decide"}.get(stage)
                if key:
                    gates[key].set()
        kwargs["_stream_event"] = event
    from contextlib import nullcontext
    # Windows process peak includes imports; report it explicitly, not a delta.
    def peak_rss():
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in
                    ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            value = Counters()
            value.cb = ctypes.sizeof(value)
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
                raise ctypes.WinError()
            return value.PeakWorkingSetSize
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    stop = threading.Event()
    sample = {"disk_peak_bytes": 0, "queue_peak": 0, "upload_queue_peak": 0}
    def sample_resources():
        while not stop.is_set():
            size = 0
            for p in work.rglob("*"):
                try:
                    if p.is_file():
                        size += p.stat().st_size
                except FileNotFoundError:
                    pass
            sample["disk_peak_bytes"] = max(sample["disk_peak_bytes"], size)
            sample["queue_peak"] = max([sample["queue_peak"]] + [q.pending for q in queues])
            sample["upload_queue_peak"] = max([sample["upload_queue_peak"]] + [len(q.pending) + len(q.reservations) for q in uploaders])
            stop.wait(.5)
    monitor = threading.Thread(target=sample_resources, daemon=True)
    monitor.start()
    start, cpu = time.perf_counter(), time.process_time()
    with (nullcontext() if profile == "old" else engine_context(config)):
        result = service._compute(iter(records) if profile.endswith("stream") else records,
            "shadow-fixed-run", "person-linh-v2-selective", **kwargs)
    metrics.update({"compute_seconds": time.perf_counter() - start, "cpu_seconds": time.process_time() - cpu,
        "peak_rss_bytes": peak_rss(), "rss_scope": "process lifetime including imports; Windows PeakWorkingSetSize",
        "storage": "LocalObjectStore; not R2 network", "db_materialization_seconds": None,
        "sampler_interval_seconds": .5})
    business = canonical_business(result)
    if hasattr(result["pairs"], "close"):
        result["pairs"].close()
    stop.set()
    monitor.join()
    metrics.update(sample)
    labels = {pair_key(a, b): v for a, b, v in json.loads((directory / (dataset + "-labels.json")).read_text(encoding="utf-8"))}
    predictions = {pair_key(p["left_id"], p["right_id"]) for p in business["pairs"] if p["triage_status"] == "HIGH_CONFIDENCE_PROPOSAL"}
    clusters = [p["member_record_ids"] for p in business["proposals"]]
    scores = {}
    if profile != "old":
        from masterone.mdm.pipeline.scoring_engine import iter_score_batches
        for rows in iter_score_batches(result["scoring_artifact"], batch):
            for row in rows:
                scores["|".join(pair_key(row.left_id, row.right_id))] = {"rule_score": row.rule_score, "raw_rule_score": row.raw_rule_score, "match_probability": row.match_probability}
    else:
        from masterone.mdm.pipeline.matching import score_pair, value_frequencies
        by_id = {r["id"]: r for r in records}
        frequencies = value_frequencies(records)
        for (a, b), reasons in candidates.items():
            scored = score_pair(by_id[a], by_id[b], reasons, frequencies)
            scores["|".join((a, b))] = {"rule_score": scored["score"], "raw_rule_score": scored.get("raw_score", scored["score"]), "match_probability": None}
    output = {"name": name, "dataset": dataset, "profile": profile, "batch": batch, "partition": partition,
        "workers": workers, "reverse": reverse, "business_hash": digest(business), "candidate_hash": digest(sorted(candidates)),
        "candidate_count": len(candidates), "business": business, "scores": scores,
        "candidates": [[a, b, sorted(reasons)] for (a, b), reasons in sorted(candidates.items())],
        "metrics": metrics, "quality": labeled_metrics(set(candidates), predictions, labels, clusters),
        "decision_counts": dict(Counter(p["decision"] for p in business["pairs"])), "events": events,
        "resource_budgets": {k: getattr(settings, k) for k in (
            "stream_partition_rows", "stream_partition_bytes", "stream_queue_bytes", "stream_queue_partitions",
            "stream_disk_bytes", "stream_timeout_seconds", "stream_consumer_workers", "stream_upload_workers")} if profile != "old" else {},
        "streaming": result.get("streaming"), "scoring_metrics": result.get("scoring_metrics")}
    write(work / "result.json", output)
    print(json.dumps({k: output[k] for k in ("name", "business_hash", "candidate_count", "metrics", "quality")}), flush=True)


def compare(a, b):
    left = {tuple(row[:2]): row[2] for row in a["candidates"]}
    right = {tuple(row[:2]): row[2] for row in b["candidates"]}
    lp = {(p["left_id"], p["right_id"]): p for p in a["business"]["pairs"]}
    rp = {(p["left_id"], p["right_id"]): p for p in b["business"]["pairs"]}
    common = set(lp) & set(rp)
    fields = ("decision", "score", "triage_status", "primary_group", "subgroup", "reason", "field_evidence")
    def memberships(r):
        return {tuple(sorted(p["member_record_ids"])): p for p in r["business"]["proposals"]}
    lc, rc = memberships(a), memberships(b)
    changes = {f: sum(lp[k].get(f) != rp[k].get(f) for k in common) for f in fields}
    score_keys = set(a["scores"]) & set(b["scores"])
    payload_fields = Counter(k for pair in common for k in set(lp[pair]) | set(rp[pair])
                            if lp[pair].get(k) != rp[pair].get(k))
    def without_batch_pin(r):
        return {**r["business"], "pairs": [{k: v for k, v in p.items() if k != "scoring_config_hash"}
                for p in r["business"]["pairs"]]}
    return {"left": a["name"], "right": b["name"], "business_equal": a["business_hash"] == b["business_hash"],
        "comparison_kind": "invariance" if a.get("profile") == "v2" and b.get("profile", "").startswith("v2")
            and b.get("partition", 0) <= 2048 else "migration_or_exploratory",
        "business_equal_except_scoring_config_hash": without_batch_pin(a) == without_batch_pin(b),
        "all_pair_payload_changes": dict(sorted(payload_fields.items())),
        "candidate_removed": len(set(left) - set(right)), "candidate_added": len(set(right) - set(left)),
        "candidate_reason_changes": sum(left[k] != right[k] for k in set(left) & set(right)),
        "pair_removed": len(set(lp) - set(rp)), "pair_added": len(set(rp) - set(lp)), "common_pair_changes": changes,
        "score_value_changes": sum(a["scores"][k]["rule_score"] != b["scores"][k]["rule_score"] for k in score_keys),
        "cluster_removed": len(set(lc) - set(rc)), "cluster_added": len(set(rc) - set(lc)),
        "golden_changes_common_membership": sum(lc[k]["golden_fields"] != rc[k]["golden_fields"] for k in set(lc) & set(rc)),
        "golden_selected_value_changes": sum(
            lc[k]["golden_fields"].get("fields", {}).get(f, {}).get("value") !=
            rc[k]["golden_fields"].get("fields", {}).get(f, {}).get("value")
            for k in set(lc) & set(rc) for f in set(lc[k]["golden_fields"].get("fields", {})) |
            set(rc[k]["golden_fields"].get("fields", {}))),
        "review_cases_equal": a["business"]["review_cases"] == b["business"]["review_cases"]}


def matrix(directory, timeout=300):
    from run_merge_baseline import isolated_environment
    runs = []
    for dataset in ("public", "internal"):
        variants = [("old", 256, 256, 1, False), ("current", 256, 256, 1, False),
            ("v2", 17, 256, 1, False), ("v2", 512, 512, 1, False)]
        variants += ([("v2-stream", 128, 128, 1, False), ("v2-stream", 256, 256, 2, True),
                      ("v2-stream", 512, 512, 2, False)] if dataset == "public" else
                     [("v2-stream", 512, 256, 1, False), ("v2-stream", 1024, 1024, 2, True),
                      ("v2-stream", 4096, 2048, 2, False)])
        for profile, batch, partition, workers, reverse in variants:
            command = [sys.executable, str(Path(__file__).resolve()), "child", "--directory", str(directory),
                "--dataset", dataset, "--profile", profile, "--batch", str(batch), "--partition", str(partition), "--workers", str(workers)]
            if reverse:
                command.append("--reverse")
            env = isolated_environment(directory / "isolated")
            # Prevent PYTHONPATH from resolving current packages in old children.
            env["PYTHONPATH"] = str(directory / "old/backend/src") if profile == "old" else str(ROOT / "backend/src")
            name = f"{dataset}-{profile}-b{batch}-p{partition}-w{workers}" + ("-reverse" if reverse else "")
            if (directory / name / "result.json").is_file():
                runs.append({"name": name, "exit_code": 0, "reused_complete_evidence": True})
                continue
            print("START " + name, flush=True)
            with (directory / (name + ".log")).open("w", encoding="utf-8") as log:
                try:
                    done = subprocess.run(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
                    runs.append({"name": name, "exit_code": done.returncode})
                except subprocess.TimeoutExpired:
                    runs.append({"name": name, "exit_code": "TIMEOUT"})
            write(directory / "execution.json", runs)
            print("END " + name + " " + str(runs[-1]["exit_code"]), flush=True)
    summary = summarize(directory)
    mismatch = any(c["comparison_kind"] == "invariance" and not c["business_equal_except_scoring_config_hash"]
                   for c in summary["comparisons"])
    return 1 if mismatch or any(row["exit_code"] != 0 for row in runs) else 0


def summarize(directory):
    results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*/result.json"))]
    comparisons = []
    for dataset in ("public", "internal"):
        rows = [r for r in results if r["dataset"] == dataset]
        base = next((r for r in rows if r["profile"] == "old"), None)
        current = next((r for r in rows if r["profile"] == "current"), None)
        v2 = next((r for r in rows if r["profile"] == "v2" and r["batch"] == 17), None)
        if base and current:
            comparisons.append(compare(base, current))
        if current and v2:
            comparisons.append(compare(current, v2))
        if v2:
            comparisons.extend(compare(v2, r) for r in rows if r["profile"].startswith("v2") and r is not v2)
    output = {"runs": [{k: r[k] for k in ("name", "profile", "dataset", "business_hash", "candidate_count", "quality", "metrics", "decision_counts")} for r in results], "comparisons": comparisons}
    write(directory / "summary.json", output)
    return output


def materialize(directory, dataset, batch):
    """Replay a measured shadow result into an owned, migrated SQLite DB."""
    sys.path.insert(0, str(ROOT / "backend/src"))
    work = directory / f"db-{dataset}-b{batch}"
    work.mkdir(exist_ok=False)
    url = "sqlite:///" + (work / "state.db").as_posix()
    os.environ["MDM_DATABASE_URL"] = url
    os.environ["EXPORT_ARTIFACT_DIR"] = str(work / "exports")
    os.environ["EXPORT_PARQUET_ENABLED"] = "true"
    from masterone.auth.models import User
    from masterone.mdm.engine_adapter import CORE_FEATURES, STREAMING_FEATURES, engine_context
    from masterone.mdm.export_stream import create_export, rss_peak
    from masterone.mdm.generation_staging import GenerationPublisher
    from masterone.mdm.migrate import upgrade_database
    from masterone.mdm.models import (
        ResolutionAuditEvent,
        ResolutionFile,
        ResolutionPair,
        ResolutionProposal,
        ResolutionRecord,
        ResolutionReviewCase,
        ResolutionRun,
        ResolutionTask,
    )
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    source = directory / f"{dataset}-v2-b17-p256-w1/result.json"
    measured = json.loads(source.read_text(encoding="utf-8"))
    records = json.loads((directory / (dataset + "-snapshot.json")).read_text(encoding="utf-8"))
    computed = {**measured["business"], "candidate_count": measured["candidate_count"],
        "insufficient_count": measured["candidate_count"] - len(measured["business"]["pairs"])}
    stages = {}
    start = time.perf_counter()
    upgrade_database(url)
    stages["fresh_migration"] = time.perf_counter() - start
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    factory = sessionmaker(engine, expire_on_commit=False)
    run_id = "shadow-fixed-run"
    start = time.perf_counter()
    with factory.begin() as db:
        db.add(User(id="shadow-owner", email="shadow-owner@example.test", email_normalized="shadow-owner@example.test", password_hash="unused", role="STEWARD"))
        db.flush()
        db.add(ResolutionRun(id=run_id, owner_user_id="shadow-owner", policy_version="person-linh-v2-selective", record_count=len(records)))
        db.flush()
        db.add(ResolutionFile(id="shadow-file", run_id=run_id, filename="snapshot.csv", sha256=file_hash(source), row_count=len(records)))
        db.flush()
        for offset in range(0, len(records), batch):
            db.add_all([ResolutionRecord(**row, run_id=run_id, file_id="shadow-file", extra_fields={}) for row in records[offset:offset+batch]])
            db.flush()
        db.add(ResolutionTask(id="shadow-task", run_id=run_id))
    stages["input_db_load"] = time.perf_counter() - start
    config = {k: True for k in CORE_FEATURES}
    config.update({k: False for k in STREAMING_FEATURES})
    def serving():
        with factory() as db:
            return {"pairs": db.query(ResolutionPair).filter_by(run_id=run_id).count(),
                "proposals": db.query(ResolutionProposal).filter_by(run_id=run_id).count(),
                "review_cases": db.query(ResolutionReviewCase).filter_by(run_id=run_id).count()}
    with engine_context(config):
        publisher = GenerationPublisher(factory, store=None, batch_size=batch)
        start = time.perf_counter()
        identity = publisher.begin(run_id, config={"shadow": measured["business_hash"]})
        stages["generation_begin"] = time.perf_counter() - start
        start = time.perf_counter()
        publisher.stage(identity, computed)
        stages["stage_and_seal"] = time.perf_counter() - start
        assert serving() == {"pairs": 0, "proposals": 0, "review_cases": 0}
        start = time.perf_counter()
        publisher.promote(identity)
        stages["validate_and_promote"] = time.perf_counter() - start
        counts = serving()
        assert counts == {k: len(v) for k, v in computed.items() if k in counts}
        from masterone.mdm.engine_adapter import EngineResultAdapter
        with factory() as db:
            actual_pairs = sorted((p.id, p.left_record_id, p.right_record_id, p.score, p.pair_decision,
                EngineResultAdapter.evidence(p.field_evidence)) for p in db.query(ResolutionPair).filter_by(run_id=run_id))
            expected_pairs = sorted((p["id"], p["left_id"], p["right_id"], p["score"], p["decision"],
                EngineResultAdapter.evidence(p["field_evidence"])) for p in computed["pairs"])
            assert actual_pairs == expected_pairs
            serving_pair_hash = digest(actual_pairs)
        start = time.perf_counter()
        publisher.stage(identity, computed)
        publisher.promote(identity)
        stages["idempotent_replay"] = time.perf_counter() - start
        assert serving() == counts
        with factory() as db:
            start = time.perf_counter()
            exported = create_export(db, run_id, "shadow-owner", True, export_id="shadow-export")
            stages["export_csv_parquet"] = time.perf_counter() - start
            receipt = db.query(ResolutionAuditEvent).filter_by(run_id=run_id, action="CSV_EXPORTED").one().details
            assert receipt["coverage"] == "PASS" and receipt["source_record_count"] == len(records)
            assert create_export(db, run_id, "shadow-owner", True, export_id="shadow-export").id == exported.id
    output = {"dataset": dataset, "batch": batch, "status": "PASS", "storage": "isolated SQLite + DB generation receipts + LocalObjectStore export",
        "stages_seconds": stages, "serving_counts": counts, "source_records": len(records),
        "peak_rss_bytes": rss_peak(), "db_bytes": (work / "state.db").stat().st_size,
        "export_receipt": receipt, "source_business_hash": measured["business_hash"],
        "serving_pair_hash": serving_pair_hash}
    write(work / "metrics.json", output)
    engine.dispose()
    print(json.dumps({k: output[k] for k in ("dataset", "batch", "status", "stages_seconds", "serving_counts", "db_bytes", "peak_rss_bytes")}), flush=True)


def rollback_reader(directory):
    """Read a real new export through the frozen legacy reader, read-only DB."""
    sys.path.insert(0, str(directory / "old/backend/src"))
    from masterone.api.mdm_resolution import _csv_response, _csv_sample
    from masterone.mdm.models import ResolutionExport
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    database = directory / "db-internal-b512/state.db"
    engine = create_engine("sqlite:///file:" + database.as_posix() + "?mode=ro&uri=true")
    with Session(engine) as db:
        snapshot = db.get(ResolutionExport, "shadow-export")
        output = {"legacy_commit": OLD, "source_rows": snapshot.row_count}
        try:
            response = _csv_response(snapshot)
            output.update({"legacy_download_bytes": len(response.body), "legacy_status_code": response.status_code,
                "status": "FAIL" if not response.body else "PASS"})
        except Exception as exc:
            output.update({"status": "FAIL", "legacy_download_error": type(exc).__name__,
                "legacy_status_code": getattr(exc, "status_code", None)})
        try:
            output["legacy_sample_rows"] = len(_csv_sample(snapshot))
        except Exception as exc:
            output["legacy_sample_error"] = type(exc).__name__
        write(directory / "rollback-reader.json", output)
        print(json.dumps(output), flush=True)
    engine.dispose()
    return 0 if output["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "matrix", "child", "summarize", "materialize", "rollback-reader"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--dataset", choices=("public", "internal"), default="public")
    parser.add_argument("--profile", choices=("old", "current", "v2", "v2-stream"), default="v2")
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--partition", type=int, default=256)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.partition <= 2048 or not 1 <= args.workers <= 8 or args.batch < 1 or args.timeout < 1:
        parser.error("Partition rows must be 1..2048, workers 1..8, and batch/timeout positive")
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT / "artifacts/merge-validation"):
        parser.error("Evidence must be inside artifacts/merge-validation")
    if args.action in {"child", "materialize", "rollback-reader"}:
        from run_merge_baseline import isolated_environment
        # Direct invocation has the same safety as the matrix driver. Explicit
        # environment values override dotenv before any runtime module imports.
        os.environ.update(isolated_environment(directory / "cli-isolated"))
    if args.action == "prepare":
        prepare(directory, args.reference)
    elif args.action == "matrix":
        raise SystemExit(matrix(directory, args.timeout))
    elif args.action == "summarize":
        summarize(directory)
    elif args.action == "materialize":
        materialize(directory, args.dataset, args.batch)
    elif args.action == "rollback-reader":
        raise SystemExit(rollback_reader(directory))
    else:
        child(directory, args.dataset, args.profile, args.batch, args.partition, args.workers, args.reverse)


if __name__ == "__main__":
    main()
