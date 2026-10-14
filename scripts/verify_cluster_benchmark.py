"""Read-only, component-scoped P-119 cluster safety benchmark.

Truth and scenario membership select/evaluate offline probes; they never enter
record features, pair assessment, scoring, or reviewer ACCEPTED decisions.
Incomplete bundles require --allow-incomplete and cannot yield a dataset PASS.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from copy import deepcopy
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def integrity(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    checked, missing, mismatches = {}, [], []
    for relative, expected in manifest["file_hashes"].items():
        path = bundle / relative
        if not path.is_file():
            missing.append(relative)
            continue
        actual = file_hash(path)
        checked[relative] = actual
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    return manifest, {
        "manifest_sha256": file_hash(bundle / "manifest.json"),
        "manifest_data_hash": manifest["data_hash"],
        "listed_files": len(manifest["file_hashes"]),
        "checked_files": len(checked),
        "matched_files": len(checked) - len(mismatches),
        "missing_files": missing,
        "mismatches": mismatches,
        "status": "PASS" if not missing and not mismatches else "INCOMPLETE_OR_CHANGED",
        "checked_sha256": checked,
    }


def read_inputs(bundle, wanted, checked):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.pair_assessment import assessment_profile, assessment_record
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.resolution_import import _iter_normalized

    records, counts, sources = {}, Counter(), Counter()
    with engine_context(assessment_profile()):
        for path in sorted((bundle / "input_p119").glob("*.csv")):
            if "input_p119/" + path.name not in checked:
                raise ValueError("Unmanifested input file")
            with path.open("rb") as stream:
                for row in _iter_normalized(stream, path.name):
                    counts[path.name] += 1
                    sources[row["source_system"]] += 1
                    key = row["source_system"] + ":" + row["external_id"]
                    if key not in wanted:
                        continue
                    if key in records:
                        raise ValueError("Duplicate selected source identity")
                    if row["benchmark_entity_id"] is not None:
                        raise ValueError("Truth column appeared in matcher input")
                    normalized, flags = standardize(
                        {field: value.strip() for field, value in row["raw_fields"].items()},
                        entity_type=row["entity_type"],
                    )
                    # Pinned identity permits order replays despite decoder UUIDs.
                    record = {"id": hashlib.sha256(key.encode()).hexdigest(),
                              "external_id": row["external_id"],
                              "source_system": row["source_system"],
                              "entity_type": row["entity_type"],
                              "raw_fields": row["raw_fields"],
                              "normalized_fields": normalized, "quality_flags": flags,
                              "extra_fields": row["extra_fields"]}
                    records[key] = assessment_record(record)
    return records, {"decoded_records": sum(counts.values()), "file_counts": dict(counts),
                     "source_counts": dict(sources), "selected_standardized_records": len(records),
                     "truth_features_passed_to_matcher": False,
                     "frequency_scope": "complete selected component, not full bundle"}


def memberships(clusters):
    return sorted(sorted(cluster["member_record_ids"]) for cluster in clusters)


def validate_offline_truth(bundle, scenarios):
    import pyarrow.parquet as pq

    expected = {key: owner for scenario in scenarios
                for key, owner in zip(scenario["record_keys"], scenario["entity_membership"])}
    matched, mismatched, junk = 0, 0, 0
    path = bundle / "truth/records.parquet"
    if not path.exists():
        return {"status": "MISSING", "expected_selected_records": len(expected)}
    for batch in pq.ParquetFile(path).iter_batches(columns=["record_key", "entity_id", "label_status"]):
        for row in batch.to_pylist():
            key = row["record_key"]
            if key not in expected:
                continue
            if row["entity_id"] == expected[key]:
                matched += 1
            else:
                mismatched += 1
            junk += row["label_status"] == "JUNK"
    return {"status": "PASS" if matched == len(expected) and not mismatched else "MISMATCH",
            "expected_selected_records": len(expected), "matched_records": matched,
            "mismatched_records": mismatched, "selected_junk_records": junk,
            "read_after_matching": True}


def safety_metrics(result, records, owners, constraints):
    from masterone.mdm.cluster_publication import with_singletons

    unresolved = set(result.get("unresolved_record_ids", result["conflict_record_ids"]))
    eligible = [record for record in records if record["id"] not in unresolved]
    published = with_singletons(eligible, result["safe_clusters"], "offline-probe", 1)
    merged = {tuple(sorted(pair)) for cluster in published
              for pair in combinations(cluster["member_record_ids"], 2)}
    positive = {tuple(sorted((a, b))) for a, b in combinations(owners, 2)
                if owners[a] is not None and owners[a] == owners[b]}
    false_pairs = {pair for pair in merged if pair not in positive}
    return {
        "published_records": sum(len(c["member_record_ids"]) for c in published),
        "unresolved_records": len(unresolved),
        "published_singletons": sum(len(c["member_record_ids"]) == 1 for c in published),
        "published_multi_record_clusters": sum(len(c["member_record_ids"]) > 1 for c in published),
        "max_published_cluster_size": max((len(c["member_record_ids"]) for c in published), default=0),
        "known_positive_pairs": len(positive), "co_clustered_positive_pairs": len(merged & positive),
        "cross_owner_or_junk_co_clustered_pairs": len(false_pairs),
        "declared_veto_or_cannot_link_co_clustered_pairs": len(merged & constraints),
        "size_limit_violations": sum(len(c["member_record_ids"]) > 20 for c in published),
        "conflicts": len(result["conflicts"]),
        "conflict_reasons": dict(Counter(c["reason"] for c in result["conflicts"])),
        "guard_visits": result.get("cluster_metrics", {}).get("visited", 0),
    }


def accumulate(total, metric):
    for key, value in metric.items():
        if isinstance(value, dict):
            target = total.setdefault(key, Counter())
            target.update(value)
        elif key == "max_published_cluster_size":
            total[key] = max(total.get(key, 0), value)
        else:
            total[key] = total.get(key, 0) + value


def evaluate_scenario(scenario, all_records, run_id, replay):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline.pair_assessment import VERSION, assess_prepared_pair, assessment_profile
    from masterone.mdm.workflow_finalize import cluster_snapshot
    from masterone.mdm.workflow_partial import partial_snapshot

    records = [all_records[key] for key in scenario["record_keys"]]
    ids = {key: record["id"] for key, record in zip(scenario["record_keys"], records)}
    oracle = scenario["safety_oracle"]
    owners = {record["id"]: owner for record, owner in zip(records, scenario["entity_membership"])}
    constraints, links, overlays = set(), frozenset(), {}
    pairs = []
    with engine_context(assessment_profile()):
        frequencies = value_frequencies(records)
        for left, right in combinations(records, 2):
            left, right = sorted((left, right), key=lambda record: record["id"])
            assessed = assess_prepared_pair(left, right, frequencies)
            assessed["id"] = hashlib.sha256(":".join(sorted((left["id"], right["id"]))).encode()).hexdigest()
            pairs.append(assessed)
        assessment_counts = {
            "decisions": dict(Counter(p["decision"] for p in pairs)),
            "primary_groups": dict(Counter(p["primary_group"] for p in pairs)),
            "triage_statuses": dict(Counter(p.get("triage_status", "NONE") for p in pairs)),
            "assessed_pairs": len(pairs),
        }
        if replay:
            if oracle.get("cannot_link"):
                links = frozenset({tuple(sorted(ids[key] for key in oracle["cannot_link"]))})
                constraints.update(links)
            if oracle.get("review_veto"):
                veto = tuple(sorted(ids[key] for key in oracle["review_veto"]))
                constraints.add(veto)
                target = next(p for p in pairs if tuple(sorted((p["left_id"], p["right_id"]))) == veto)
                overlays[target["id"]] = "REJECTED"
        snapshot = {"records": records, "pairs": pairs, "profile": assessment_profile(),
                    "overlays": overlays, "negative_pairs": sorted(links), "policy_version": VERSION}
        full = cluster_snapshot(deepcopy(snapshot), run_id)
        partial = partial_snapshot(deepcopy(snapshot), run_id, cluster_snapshot)
        metrics = {"full": safety_metrics(full, records, owners, constraints),
                   "partial": safety_metrics(partial, records, owners, constraints)}
        # No supplied scenario declares ACCEPTED. Only explicit negative decisions replay.
        if not replay or not scenario.get("evidence_edges"):
            return assessment_counts, metrics, None
        edge_keys = {tuple(sorted((ids[e["left"]], ids[e["right"]]))): e
                     for e in scenario["evidence_edges"]}
        probe_pairs = []
        for assessed in pairs:
            key = tuple(sorted((assessed["left_id"], assessed["right_id"])))
            if key in edge_keys:
                # A separate guard stress assumption, never a reviewer acceptance or engine finding.
                probe_pairs.append({**assessed, "decision": "MERGEABLE",
                                    "triage_status": "HIGH_CONFIDENCE_PROPOSAL",
                                    "match_weight": edge_keys[key]["weight"]})
            elif assessed["id"] in overlays:
                probe_pairs.append(assessed)
        probe_snapshot = {**snapshot, "pairs": probe_pairs}
        guard = cluster_snapshot(deepcopy(probe_snapshot), run_id)
        guard_partial = partial_snapshot(deepcopy(probe_snapshot), run_id, cluster_snapshot)
        reversed_snapshot = {**probe_snapshot, "records": list(reversed(records)),
                             "pairs": list(reversed(probe_pairs))}
        permuted = cluster_snapshot(deepcopy(reversed_snapshot), run_id)
        probe_metrics = {"full": safety_metrics(guard, records, owners, constraints),
                         "partial": safety_metrics(guard_partial, records, owners, constraints),
                         "order_invariant": memberships(guard["safe_clusters"]) == memberships(permuted["safe_clusters"])}
        return assessment_counts, metrics, probe_metrics


def benchmark(bundle, allow_incomplete, per_stratum=None, max_component_size=None):
    started = time.monotonic()
    manifest, checked = integrity(bundle)
    report = {"bundle": str(bundle), "case": manifest["case"], "target_records": manifest["target_records"],
              "parameter_provenance": manifest["parameter_provenance"], "realism_level": manifest["realism_level"],
              "integrity": checked}
    mismatched = {item["path"] for item in checked["mismatches"]}
    scenario_path = bundle / "evaluation/scenarios.jsonl"
    if (checked["status"] != "PASS" and not allow_incomplete) or not scenario_path.exists():
        report.update(status="BLOCKED_INTEGRITY", seconds=time.monotonic() - started)
        return report
    used_paths = {"evaluation/scenarios.jsonl"} | {"input_p119/" + path.name for path in (bundle / "input_p119").glob("*.csv")}
    if used_paths & mismatched:
        report.update(status="BLOCKED_CHANGED_INPUT", seconds=time.monotonic() - started)
        return report
    scenarios, selected_counts, declared_counts = [], Counter(), Counter()
    with scenario_path.open(encoding="utf-8") as stream:
        for line in stream:
            scenario = json.loads(line)
            stratum = scenario["stratum"]
            declared_counts[stratum] += 1
            # Inspect a bounded reservoir so a missing partition does not bias
            # the probe toward only the first few incomplete components.
            if per_stratum is not None and selected_counts[stratum] >= per_stratum * 10:
                continue
            if max_component_size is not None and len(scenario["record_keys"]) > max_component_size:
                continue
            scenarios.append(scenario)
            selected_counts[stratum] += 1
    wanted = {key for scenario in scenarios for key in scenario["record_keys"]}
    records, decoder = read_inputs(bundle, wanted, checked["checked_sha256"])
    decoder["available_file_counts_match_manifest"] = all(
        count == manifest["file_counts"][filename] for filename, count in decoder["file_counts"].items())
    report.update(decoder=decoder, declared_scenarios_by_stratum=dict(declared_counts),
                  selected_scenarios_by_stratum=dict(selected_counts))
    complete, skipped, executed, failures = Counter(), Counter(), Counter(), Counter()
    engine, constrained, probes, assessments = {}, {}, {}, {}
    deterministic = 0
    for index, scenario in enumerate(scenarios):
        stratum = scenario["stratum"]
        if not set(scenario["record_keys"]) <= records.keys():
            skipped[stratum] += 1
            continue
        if per_stratum is not None and complete[stratum] >= per_stratum:
            continue
        complete[stratum] += 1
        try:
            assessed, plain, _ = evaluate_scenario(scenario, records, "offline-" + str(index), False)
            _, replay, probe = evaluate_scenario(scenario, records, "offline-" + str(index), True)
        except (ValueError, TypeError, KeyError) as error:
            # Aggregate codes only: never leak an endpoint or input field via traceback.
            failures[stratum + ":" + type(error).__name__] += 1
            continue
        executed[stratum] += 1
        accumulate(assessments.setdefault(stratum, {}), assessed)
        for mode, metric in plain.items():
            accumulate(engine.setdefault(stratum, {}).setdefault(mode, {}), metric)
        for mode, metric in replay.items():
            accumulate(constrained.setdefault(stratum, {}).setdefault(mode, {}), metric)
        if probe is not None:
            deterministic += int(probe["order_invariant"])
            for mode in ("full", "partial"):
                accumulate(probes.setdefault(stratum, {}).setdefault(mode, {}), probe[mode])
    report.update(status="QUALIFIED_COMPONENT_PROBES" if checked["status"] != "PASS" else "COMPONENT_PROBES_COMPLETE",
                  complete_scenarios_by_stratum=dict(complete), excluded_incomplete_scenarios_by_stratum=dict(skipped),
                  executed_scenarios_by_stratum=dict(executed), runtime_failures_by_stratum_and_type=dict(failures),
                  assessed_pairs_by_stratum=assessments, actual_undecided_assessment=engine,
                  declared_negative_decision_replay=constrained, assumed_eligible_scenario_edge_guard_probes=probes,
                  order_invariant_guard_probes=deterministic, seconds=time.monotonic() - started)
    report["offline_truth_mapping"] = validate_offline_truth(bundle, scenarios)
    for section in (engine, constrained, probes):
        for modes in section.values():
            for metric in modes.values():
                metric["positive_co_cluster_recall"] = (metric["co_clustered_positive_pairs"] / metric["known_positive_pairs"]
                                                        if metric["known_positive_pairs"] else None)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--tc13-per-stratum", type=int, default=3)
    parser.add_argument("--check-tc6-1m-hash", action="store_true")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.datasets.resolve()):
        parser.error("Output must be outside the read-only benchmark datasets")
    report = {"version": 1, "scope": "Offline complete-component probes; no service, database, LLM, production promotion, or threshold calibration",
              "reviewer_accepted_decisions": 0,
              "limitations": ["Synthetic PRIOR/L0 cannot establish production auto-merge thresholds.",
                              "Components evaluated independently with local frequencies and fresh per-component guard budgets.",
                              "Scenario edges are evaluation fixtures, not candidate retrieval or reviewer approval.",
                              "Assumed-eligible guard probes force MERGEABLE/routing only on declared evidence edges; actual field comparisons and groups remain.",
                              "All actual assessment overlays stay UNDECIDED except declared REJECTED vetoes; no truth-derived ACCEPTED decisions.",
                              "False-merge zero with zero multi-record publication is abstention, not evidence of useful merge recall.",
                              "No full-bundle candidate recall, performance, queue, import publication, export, or learned-model evaluation."],
              "bundles": []}
    code_paths = ["backend/src/masterone/mdm/pipeline/pair_assessment.py", "backend/src/masterone/mdm/pipeline/standardization.py",
                  "backend/src/masterone/mdm/pipeline/clustering.py", "backend/src/masterone/mdm/pipeline/cluster_guards.py",
                  "backend/src/masterone/mdm/pipeline/decision_semantics.py", "backend/src/masterone/mdm/pipeline/matching.py",
                  "backend/src/masterone/mdm/workflow_finalize.py", "backend/src/masterone/mdm/workflow_partial.py",
                  "backend/src/masterone/mdm/resolution_import.py"]
    report["code_sha256_at_start"] = {path: file_hash(ROOT / path) for path in code_paths}
    report["script_sha256_at_start"] = file_hash(Path(__file__))
    for case, scale, limit, maximum in (("TC6_CLUSTER_BRIDGE", "100k", None, None),
                                        ("TC8_JUNK_PLACEHOLDER", "100k", None, None),
                                        ("TC13_HOT_BLOCK_RETRIEVAL", "100k", args.tc13_per_stratum, 61),
                                        ("TC13_HOT_BLOCK_RETRIEVAL", "1m", args.tc13_per_stratum, 61)):
        result = benchmark(args.datasets / case / scale / "seed-7", args.allow_incomplete, limit, maximum)
        report["bundles"].append(result)
        print(json.dumps({"case": result["case"], "scale": scale, "status": result["status"],
                          "complete_components": sum(result.get("complete_scenarios_by_stratum", {}).values()),
                          "seconds": result["seconds"]}), flush=True)
    if args.check_tc6_1m_hash:
        bundle = args.datasets / "TC6_CLUSTER_BRIDGE/1m/seed-7"
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        relative = "input_p119/csv-1.csv"
        actual = file_hash(bundle / relative) if (bundle / relative).exists() else None
        report["tc6_1m_known_hash_check"] = {"path": relative, "expected": manifest["file_hashes"][relative],
                                             "actual": actual, "matches": actual == manifest["file_hashes"][relative],
                                             "used_for_matching": False}
    report["script_sha256"] = file_hash(Path(__file__))
    report["code_sha256_at_finish"] = {path: file_hash(ROOT / path) for path in code_paths}
    report["code_unchanged_during_run"] = (report["code_sha256_at_start"] == report["code_sha256_at_finish"]
                                           and report["script_sha256_at_start"] == report["script_sha256"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Aggregate report written: " + str(args.output), flush=True)
    failed = any(bundle.get("runtime_failures_by_stratum_and_type") for bundle in report["bundles"])
    failed |= any(bundle.get("offline_truth_mapping", {}).get("status") == "MISMATCH" for bundle in report["bundles"])
    for bundle in report["bundles"]:
        for section in ("actual_undecided_assessment", "declared_negative_decision_replay",
                        "assumed_eligible_scenario_edge_guard_probes"):
            for modes in bundle.get(section, {}).values():
                for metric in modes.values():
                    failed |= bool(metric["cross_owner_or_junk_co_clustered_pairs"]
                                   or metric["declared_veto_or_cannot_link_co_clustered_pairs"]
                                   or metric["size_limit_violations"])
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
