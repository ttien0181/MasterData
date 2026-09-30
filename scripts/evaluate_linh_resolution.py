"""Offline evaluation for Linh's PERSON benchmark; labels never enter product APIs."""

import argparse
import csv
import itertools
import json
from collections import defaultdict
from pathlib import Path

from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.matching import score_pair
from masterone.mdm.pipeline.standardization import standardize


def load_inputs(data_dir: Path):
    records = []
    entity_by_id = {}
    with (data_dir / "records.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            record_id = row["id"]
            raw = {"name": row.get("full_name", ""), "phone": row.get("phone_number", ""),
                   "email": row.get("email_address", ""), "address": row.get("raw_address", "")}
            normalized, flags = standardize(raw)
            records.append({"id": record_id, "external_id": record_id,
                            "source_system": row["source_system"].upper(),
                            "raw_fields": raw, "normalized_fields": normalized, "quality_flags": flags})
            entity_by_id[record_id] = row.get("entity_id", "")
    labeled = []
    with (data_dir / "pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            labeled.append({"left_id": row["id_a"], "right_id": row["id_b"],
                            "is_match": int(row["is_match"]),
                            "hard_negative_type": row.get("hard_negative_type", "")})
    return records, entity_by_id, labeled


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def evaluate(data_dir: Path) -> dict:
    records, entity_by_id, labeled = load_inputs(data_dir)
    blocks = generate_candidates(records)
    by_id = {record["id"]: record for record in records}
    scored = []
    for (left_id, right_id), reasons in blocks.items():
        scored.append({"id": f"{left_id}:{right_id}", "left_id": left_id, "right_id": right_id,
                      **score_pair(by_id[left_id], by_id[right_id], reasons)})
    clusters = build_proposals(records, scored, "offline", "person-v1")
    candidate_keys = set(blocks)
    positive_by_scope = {"same_source": set(), "cross_source": set()}
    entities: dict[str, list[str]] = defaultdict(list)
    for record_id, entity_id in entity_by_id.items():
        if entity_id:
            entities[entity_id].append(record_id)
    for member_ids in entities.values():
        for left_id, right_id in itertools.combinations(sorted(member_ids), 2):
            scope = "same_source" if by_id[left_id]["source_system"] == by_id[right_id]["source_system"] else "cross_source"
            positive_by_scope[scope].add((left_id, right_id))
    recall = {}
    for scope, pairs in positive_by_scope.items():
        recall[scope] = {"gold_pair_count": len(pairs),
                         "blocked_pair_count": sum(pair in candidate_keys for pair in pairs),
                         "blocking_recall": ratio(sum(pair in candidate_keys for pair in pairs), len(pairs))}

    labeled_stats = {"pair_count": len(labeled), "positive_count": sum(row["is_match"] == 1 for row in labeled),
                     "negative_count": sum(row["is_match"] == 0 for row in labeled)}
    decisions = {(pair["left_id"], pair["right_id"]): pair["decision"] for pair in scored}
    tp = fp = fn = 0
    hard_false_positive = 0
    hard_negative_count = 0
    for row in labeled:
        key = tuple(sorted((row["left_id"], row["right_id"])))
        predicted = decisions.get(key) == "MATCH_CANDIDATE"
        if row["is_match"] and predicted:
            tp += 1
        elif not row["is_match"] and predicted:
            fp += 1
            if row["hard_negative_type"]:
                hard_false_positive += 1
        elif row["is_match"] and not predicted:
            fn += 1
        if row["hard_negative_type"]:
            hard_negative_count += 1

    predicted_pairs = set()
    for proposal in clusters["proposals"]:
        predicted_pairs.update(itertools.combinations(proposal["member_record_ids"], 2))
    gold_pairs = positive_by_scope["same_source"] | positive_by_scope["cross_source"]
    cluster_tp = len(predicted_pairs & gold_pairs)
    cluster_fp = len(predicted_pairs - gold_pairs)
    cluster_fn = len(gold_pairs - predicted_pairs)
    cluster_precision = ratio(cluster_tp, cluster_tp + cluster_fp)
    cluster_recall = ratio(cluster_tp, cluster_tp + cluster_fn)
    cluster_f1 = ratio(2 * cluster_precision * cluster_recall,
                       (cluster_precision or 0) + (cluster_recall or 0)) if cluster_precision is not None else None
    all_possible = len(records) * (len(records) - 1) // 2
    return {
        "dataset": str(data_dir), "entity_type": "PERSON", "policy_version": "person-v1",
        "record_count": len(records), "source_counts": dict(sorted(
            (source, sum(record["source_system"] == source for record in records))
            for source in {record["source_system"] for record in records})),
        "candidate_count": len(candidate_keys), "all_possible_pairs": all_possible,
        "reduction_ratio": ratio(all_possible - len(candidate_keys), all_possible),
        "blocking_recall": recall, "labeled_pair_metrics": {
            **labeled_stats, "match_precision": ratio(tp, tp + fp), "match_recall": ratio(tp, tp + fn),
            "hard_negative_count": hard_negative_count,
            "hard_negative_false_positive_rate": ratio(hard_false_positive, hard_negative_count)},
        "cluster_pair_metrics": {"predicted_positive_pairs": len(predicted_pairs), "gold_positive_pairs": len(gold_pairs),
                                 "precision": cluster_precision, "recall": cluster_recall, "f1": cluster_f1},
        "limitations": ["pairs.csv là tập nhãn được lấy mẫu, không đại diện mọi cặp.",
                        "entity_id chỉ được dùng để chấm điểm offline; không truyền vào pipeline matching."],
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
