"""Experimental offline rule-score sweep; never configure production or approve review."""

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def wilson(successes, trials):
    if not trials:
        return None
    z = 1.959963984540054
    estimate = successes / trials
    scale = 1 + z * z / trials
    middle = (estimate + z * z / (2 * trials)) / scale
    width = z * math.sqrt(estimate * (1 - estimate) / trials + z * z / (4 * trials * trials)) / scale
    return [max(0, middle - width), min(1, middle + width)]


def inputs(bundle, overlay, wanted):
    from masterone.mdm.pipeline.pair_assessment import assessment_record
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.resolution_import import _iter_normalized

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    verified, records, counts = [], {}, Counter()
    for relative, expected in manifest["file_hashes"].items():
        if not relative.startswith("input_p119/"):
            continue
        source = bundle / relative
        if not source.exists():
            source = overlay / relative
        actual = sha256(source)
        if actual != expected:
            raise ValueError("Input SHA-256 mismatch: " + relative)
        verified.append({"relative": relative, "sha256": actual, "workspace_recovery": source.is_relative_to(overlay)})
        with source.open("rb") as stream:
            for row in _iter_normalized(stream, source.name):
                counts[source.name] += 1
                key = row["source_system"] + ":" + row["external_id"]
                if key not in wanted:
                    continue
                if row["benchmark_entity_id"] is not None or key in records:
                    raise ValueError("Input truth column or duplicate identity")
                nf, flags = standardize({key: value.strip() for key, value in row["raw_fields"].items()}, row["entity_type"])
                records[key] = assessment_record({"id": hashlib.sha256(key.encode()).hexdigest(),
                                                  "external_id": row["external_id"], "source_system": row["source_system"],
                                                  "entity_type": row["entity_type"], "raw_fields": row["raw_fields"],
                                                  "normalized_fields": nf, "quality_flags": flags,
                                                  "extra_fields": row["extra_fields"]})
    if set(counts) != set(manifest["file_counts"]) or dict(counts) != manifest["file_counts"]:
        raise ValueError("Input record counts mismatch")
    return records, {"decoded_records": sum(counts.values()), "selected_records": len(records),
                     "verified_inputs": verified, "counts": dict(counts)}


def prepare(bundle, overlay):
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline.pair_assessment import assess_prepared_pair

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for relative in ("evaluation/scenarios.jsonl", "truth/records.parquet"):
        if sha256(bundle / relative) != manifest["file_hashes"][relative]:
            raise ValueError("Evaluation SHA-256 mismatch")
    scenarios = [json.loads(line) for line in (bundle / "evaluation/scenarios.jsonl").read_text(encoding="utf-8").splitlines()]
    wanted = {key for scenario in scenarios for key in scenario["record_keys"]}
    records, integrity = inputs(bundle, overlay, wanted)
    components, scores, production_counts = [], [], Counter()
    for scenario in scenarios:
        if not set(scenario["record_keys"]) <= records.keys():
            raise ValueError("Incomplete scenario after recovery")
        members = [records[key] for key in scenario["record_keys"]]
        frequencies, pairs = value_frequencies(members), []
        for left, right in combinations(sorted(members, key=lambda record: record["id"]), 2):
            result = assess_prepared_pair(left, right, frequencies)
            result["id"] = hashlib.sha256((left["id"] + ":" + right["id"]).encode()).hexdigest()
            pairs.append(result)
            scores.append(result["score"])
            production_counts[result["decision"] + ":" + result.get("triage_status", "NONE")] += 1
        ids = {key: records[key]["id"] for key in scenario["record_keys"]}
        oracle = scenario["safety_oracle"]
        links = frozenset({tuple(sorted(ids[key] for key in oracle["cannot_link"]))}) if oracle.get("cannot_link") else frozenset()
        vetoes = frozenset({tuple(sorted(ids[key] for key in oracle["review_veto"]))}) if oracle.get("review_veto") else frozenset()
        components.append({"records": members, "pairs": pairs, "links": links, "vetoes": vetoes,
                           "stratum": scenario["stratum"], "source_keys": scenario["record_keys"]})
    # Truth is loaded only after every raw-input assessment has completed.
    import pyarrow.parquet as pq

    truth = {row["record_key"]: row["entity_id"] for row in pq.read_table(
        bundle / "truth/records.parquet", columns=["record_key", "entity_id"]).to_pylist() if row["record_key"] in wanted}
    for scenario, component in zip(scenarios, components):
        if [truth[key] for key in scenario["record_keys"]] != scenario["entity_membership"]:
            raise ValueError("Truth/scenario mapping mismatch")
        component["owners"] = {record["id"]: truth[key] for record, key in zip(component["records"], component["source_keys"])}
    return components, scores, {"case": manifest["case"], "components": len(components),
                                "strata": dict(Counter(component["stratum"] for component in components)),
                                "integrity": integrity, "production_assessment_counts": dict(production_counts)}


def anchored(pair):
    states = {field: value["state"] for field, value in pair["comparisons"].items()}
    return (states.get("government_id") == "EX" or
            states.get("name") in {"EX", "NX", "NA", "HS"}
            and any(states.get(field) in {"EX", "NX"} for field in ("phone", "email")))


def sweep_component(component, threshold, run_id):
    from masterone.mdm.cluster_publication import with_singletons
    from masterone.mdm.pipeline.clustering import build_proposals

    owners, links, vetoes = component["owners"], component["links"], component["vetoes"]
    positives = {tuple(sorted((a, b))) for a, b in combinations(owners, 2)
                 if owners[a] is not None and owners[a] == owners[b]}
    pairs, predicted = [], set()
    for observed in component["pairs"]:
        endpoints = tuple(sorted((observed["left_id"], observed["right_id"])))
        eligible = (observed["score"] >= threshold and observed["primary_group"] == "A" and anchored(observed)
                    and observed["decision"] not in {"BLOCKED", "NOT_MERGEABLE", "NON_MATCH", "INSUFFICIENT_EVIDENCE"}
                    and endpoints not in links | vetoes)
        pairs.append({**observed, "decision": "MERGEABLE" if eligible else observed["decision"],
                      "triage_status": "HIGH_CONFIDENCE_PROPOSAL" if eligible else "REVIEW",
                      "reason": "EXPERIMENTAL_SCORE_SWEEP" if eligible else observed["reason"]})
        if eligible:
            predicted.add(endpoints)
        else:
            # The legacy builder selects MERGEABLE solely by conclusion; an
            # excluded experimental edge must therefore explicitly become REVIEW.
            pairs[-1]["decision"] = "REVIEW" if observed["decision"] == "MERGEABLE" else observed["decision"]
    computed = build_proposals(component["records"], pairs, run_id, "experimental-offline-score-sweep",
                               negative_pairs=links, execution_blocked_pairs=vetoes)
    clusters = with_singletons(component["records"], computed["proposals"], run_id, 1)
    merged = {tuple(sorted(pair)) for cluster in clusters for pair in combinations(cluster["member_record_ids"], 2)}
    truth_groups = defaultdict(set)
    for record_id, owner in owners.items():
        truth_groups[owner if owner is not None else record_id].add(record_id)
    predicted_groups = [set(cluster["member_record_ids"]) for cluster in clusters]
    precision_sum, recall_sum = 0.0, 0.0
    exact = 0
    for group in predicted_groups:
        if group in truth_groups.values():
            exact += 1
        for record_id in group:
            truth_group = truth_groups[owners[record_id] if owners[record_id] is not None else record_id]
            overlap = len(group & truth_group)
            precision_sum += overlap / len(group)
            recall_sum += overlap / len(truth_group)
    return {"pair_predicted": len(predicted), "pair_tp": len(predicted & positives),
            "pair_fp": len(predicted - positives), "positive_pairs": len(positives),
            "negative_pairs": len(component["pairs"]) - len(positives),
            "cluster_merged_pairs": len(merged), "cluster_tp_pairs": len(merged & positives),
            "cluster_fp_pairs": len(merged - positives), "constraint_violations": len(merged & (links | vetoes)),
            "multirecord_clusters": sum(len(group) > 1 for group in predicted_groups),
            "size_violations": sum(len(group) > 20 for group in predicted_groups),
            "exact_predicted_clusters": exact, "predicted_clusters": len(predicted_groups),
            "truth_clusters": len(truth_groups), "records": len(owners),
            "bcubed_precision_sum": precision_sum, "bcubed_recall_sum": recall_sum,
            "components": 1, "components_with_false_merge": int(bool(merged - positives)),
            "guard_reasons": Counter(pair.get("triage_reason", "NONE") for pair in pairs
                                     if pair.get("triage_status") == "REVIEW" and pair.get("triage_reason", "").startswith("CLUSTER_"))}


def finish(metric):
    def fraction(a, b):
        return metric[a] / metric[b] if metric[b] else None
    metric.update(pair_precision=fraction("pair_tp", "pair_predicted"), pair_recall=fraction("pair_tp", "positive_pairs"),
                  pair_precision_wilson95=wilson(metric["pair_tp"], metric["pair_predicted"]),
                  cluster_pair_precision=fraction("cluster_tp_pairs", "cluster_merged_pairs"),
                  cluster_pair_recall=fraction("cluster_tp_pairs", "positive_pairs"),
                  cluster_pair_precision_wilson95=wilson(metric["cluster_tp_pairs"], metric["cluster_merged_pairs"]),
                  exact_cluster_precision=fraction("exact_predicted_clusters", "predicted_clusters"),
                  exact_cluster_recall=fraction("exact_predicted_clusters", "truth_clusters"),
                  bcubed_precision=fraction("bcubed_precision_sum", "records"),
                  bcubed_recall=fraction("bcubed_recall_sum", "records"),
                  false_merge_component_rate_wilson95=wilson(metric["components_with_false_merge"], metric["components"]))
    return metric


def add(target, metric):
    for key, value in metric.items():
        if key == "guard_reasons":
            target.setdefault(key, Counter()).update(value)
        else:
            target[key] = target.get(key, 0) + value


def validation_exit_code(report):
    violations = any(item["aggregate"]["constraint_violations"] or item["aggregate"]["size_violations"]
                     for bundle in report["bundles"] for item in bundle["sweep"])
    return int(not report["code_unchanged"] or violations)


def main():
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.pair_assessment import assessment_profile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.datasets.resolve()):
        parser.error("Output must be outside readonly input")
    started = time.monotonic()
    report = {"scope": "Experimental synthetic PRIOR/L0 descriptive threshold sweep; no production configuration or selected risk target",
              "threshold_selected_for_production": None, "reviewer_acceptances": 0,
              "experimental_gate": "Actual rule score >= threshold; Group A; direct identifier or good-name/contact anchor; no blocked/insufficient evidence or declared veto/cannot-link",
              "truth_used_for_scoring": False, "learned_parameters": False,
              "limitations": ["Thresholds derive only from unlabelled observed rule-score quantiles, not a trained or calibrated probability model.",
                              "Repeated descriptive sweeps on same components are not an independent holdout validation.",
                              "Pair Wilson intervals are descriptive; within-component pair outcomes are dependent. Component interval is primary safety support.",
                              "No real-world transfer guarantee; synthetic empirical zero false merges cannot certify production safety.",
                              "Component-local frequencies/budgets and exhaustive within-component candidate universe; no full-bundle retrieval/performance claim.",
                              "Ownership positives include deliberately vetoed pairs; identity recall and permission compliance are separate.",
                              "TC8 scenarios include placeholder controls, not JUNK rows; no JUNK-detector certification."], "bundles": []}
    code_files = [Path(__file__), ROOT / "backend/src/masterone/mdm/pipeline/pair_assessment.py",
                  ROOT / "backend/src/masterone/mdm/pipeline/clustering.py", ROOT / "backend/src/masterone/mdm/pipeline/cluster_guards.py",
                  ROOT / "backend/src/masterone/mdm/pipeline/matching.py", ROOT / "backend/src/masterone/mdm/pipeline/decision_semantics.py"]
    report["code_sha256_start"] = {str(path.relative_to(ROOT)): sha256(path) for path in code_files}
    with engine_context(assessment_profile()):
        prepared, all_scores = [], []
        for case in ("TC6_CLUSTER_BRIDGE", "TC8_JUNK_PLACEHOLDER"):
            components, scores, metadata = prepare(args.datasets / case / "100k/seed-7", args.cache / case / "100k/seed-7")
            prepared.append((components, metadata))
            all_scores.extend(scores)
            print(json.dumps({"case": metadata["case"], "components": len(components), "assessed_pairs": len(scores)}), flush=True)
        ordered = sorted(set(all_scores))
        thresholds = sorted({ordered[0], ordered[-1] + 0.000001} | {ordered[round(q * (len(ordered) - 1))] for q in (0.1, 0.25, 0.5, 0.75, 0.9)})
        report["thresholds"] = thresholds
        report["score_support"] = {"pair_count": len(all_scores), "distinct_values": len(ordered), "min": ordered[0], "max": ordered[-1]}
        for components, metadata in prepared:
            metadata["sweep"] = []
            for threshold in thresholds:
                totals, strata = {}, {}
                for index, component in enumerate(components):
                    metric = sweep_component(component, threshold, "experiment-" + str(index))
                    add(totals, metric)
                    add(strata.setdefault(component["stratum"], {}), metric)
                metadata["sweep"].append({"threshold": threshold, "aggregate": finish(totals),
                                          "by_stratum": {key: finish(value) for key, value in strata.items()}})
                print(json.dumps({"case": metadata["case"], "threshold": threshold,
                                  "pair_precision": totals["pair_precision"], "cluster_false_pairs": totals["cluster_fp_pairs"],
                                  "cluster_recall": totals["cluster_pair_recall"]}), flush=True)
            report["bundles"].append(metadata)
    report["code_sha256_finish"] = {str(path.relative_to(ROOT)): sha256(path) for path in code_files}
    report["code_unchanged"] = report["code_sha256_start"] == report["code_sha256_finish"]
    report["seconds"] = time.monotonic() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Wrote aggregate calibration report: " + str(args.output), flush=True)
    sys.exit(validation_exit_code(report))


if __name__ == "__main__":
    main()
