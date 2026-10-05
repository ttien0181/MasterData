"""Evaluate the live policy against Linh's labels without feeding labels to matching."""

import argparse
import csv
import itertools
import json
from collections import defaultdict
from pathlib import Path

from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.matching import POLICY_VERSION
from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.resolution_service import _compute


def load_inputs(data_dir: Path):
    paths = [data_dir / name for name in ("crm.csv", "erp.csv", "bill.csv")]
    if not all(path.is_file() for path in paths):
        paths = [data_dir / "records.csv"]
    records, labels = [], {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                linh = "record_id" in row
                record_id = row["record_id" if linh else "id"]
                raw = ({field: row.get(field, "") for field in ("name", "phone", "email", "address")}
                       if linh else {"name": row.get("full_name", ""), "phone": row.get("phone_number", ""),
                                     "email": row.get("email_address", ""), "address": row.get("raw_address", "")})
                normalized, flags = standardize(raw)
                records.append({"id": record_id, "external_id": record_id,
                                "source_system": row["source" if linh else "source_system"].upper(),
                                "raw_fields": raw, "normalized_fields": normalized, "quality_flags": flags})
                labels[record_id] = row.get("entity_id", "")
    return records, labels


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def evaluate(data_dir: Path) -> dict:
    records, labels = load_inputs(data_dir)
    by_id = {row["id"]: row for row in records}
    if len(by_id) != len(records):
        raise ValueError("record_id trùng trong bộ dữ liệu đánh giá")
    # Labels are kept separate. `_compute` receives exactly the fields used by the API.
    candidate_keys = set(generate_candidates(records))
    result = _compute(records, "offline", POLICY_VERSION)
    entities = defaultdict(list)
    for record_id, entity_id in labels.items():
        if entity_id:
            entities[entity_id].append(record_id)
    gold_pairs = set()
    by_scope = {"same_source": set(), "cross_source": set()}
    for members in entities.values():
        for left, right in itertools.combinations(sorted(members), 2):
            key = (left, right)
            gold_pairs.add(key)
            scope = "same_source" if by_id[left]["source_system"] == by_id[right]["source_system"] else "cross_source"
            by_scope[scope].add(key)

    subgroups = defaultdict(lambda: {"candidate_count": 0, "true_pairs": 0,
                                    "auto_pairs": 0, "auto_true_pairs": 0, "review_pairs": 0})
    auto_pairs = set()
    for pair in result["pairs"]:
        key = tuple(sorted((pair["left_id"], pair["right_id"])))
        counts = subgroups[pair["subgroup"] or "UNCLASSIFIED"]
        counts["candidate_count"] += 1
        counts["true_pairs"] += key in gold_pairs
        if pair["triage_status"] == "HIGH_CONFIDENCE_PROPOSAL":
            auto_pairs.add(key)
            counts["auto_pairs"] += 1
            counts["auto_true_pairs"] += key in gold_pairs
        elif pair["triage_status"] == "REVIEW":
            counts["review_pairs"] += 1

    merged_pairs = set()
    impure_clusters = 0
    for proposal in result["proposals"]:
        members = proposal["member_record_ids"]
        merged_pairs.update(itertools.combinations(members, 2))
        impure_clusters += len({labels[member] for member in members}) > 1
    merged_true = len(merged_pairs & gold_pairs)
    auto_true = len(auto_pairs & gold_pairs)
    return {
        "dataset": str(data_dir), "entity_type": "PERSON", "policy_version": POLICY_VERSION,
        "record_count": len(records), "source_counts": {source: sum(row["source_system"] == source for row in records)
                                                       for source in sorted({row["source_system"] for row in records})},
        "gold_entity_count": len(entities), "gold_positive_pair_count": len(gold_pairs),
        "candidate_count": len(candidate_keys), "scored_pair_count": len(result["pairs"]),
        "insufficient_evidence_count": result["insufficient_count"],
        "all_possible_pairs": len(records) * (len(records) - 1) // 2,
        "blocking_recall": {scope: {"gold_pair_count": len(pairs),
                                    "candidate_pair_count": len(pairs & candidate_keys),
                                    "recall": ratio(len(pairs & candidate_keys), len(pairs))}
                            for scope, pairs in by_scope.items()},
        "auto_pair_metrics": {"predicted_count": len(auto_pairs), "true_positive": auto_true,
                              "false_positive": len(auto_pairs) - auto_true,
                              "precision": ratio(auto_true, len(auto_pairs)),
                              "recall": ratio(auto_true, len(gold_pairs))},
        "cluster_pair_metrics": {"predicted_count": len(merged_pairs), "true_positive": merged_true,
                                 "false_positive": len(merged_pairs) - merged_true,
                                 "precision": ratio(merged_true, len(merged_pairs)),
                                 "recall": ratio(merged_true, len(gold_pairs)),
                                 "impure_cluster_count": impure_clusters},
        "subgroups": {name: {**counts,
                             "auto_precision": ratio(counts["auto_true_pairs"], counts["auto_pairs"])}
                      for name, counts in sorted(subgroups.items())},
        "limitations": ["entity_id chỉ dùng để chấm điểm offline, không truyền vào matching.",
                        "Precision trên bộ dữ liệu tổng hợp không bảo đảm cho dữ liệu doanh nghiệp thật."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("masterone_test"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.data_dir)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
