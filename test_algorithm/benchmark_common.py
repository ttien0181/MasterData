"""Shared data splitting and evaluation for the record-linkage benchmarks."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from data import DatasetFactory

Pair = tuple[str, str]


def pair_key(left: Any, right: Any) -> Pair:
    # Chuẩn hóa thứ tự ID để (A, B) và (B, A) được tính là cùng một cặp.
    left, right = str(left), str(right)
    return (left, right) if left < right else (right, left)


@dataclass
class Partition:
    rows: dict[str, dict[str, Any]]
    left: dict[str, dict[str, Any]]
    right: dict[str, dict[str, Any]] | None
    true_pairs: set[Pair]

    @property
    def is_linkage(self) -> bool:
        return self.right is not None


def load_splits(dataset_name: str, seed: int = 42) -> dict[str, Partition]:
    """Split by entity so records from one entity cannot cross partitions."""
    records, entity_by_id = DatasetFactory.get_loader(dataset_name).load()
    ids = [record.source_id for record in records]
    if len(ids) != len(set(ids)) or set(ids) != set(entity_by_id):
        raise ValueError(f"{dataset_name}: record IDs and truth IDs must be unique and equal")

    rows = {
        record.source_id: {
            "unique_id": record.source_id,
            "given_name": record.given_name,
            "surname": record.surname,
            "date_of_birth": record.date_of_birth,
            "postcode": record.postcode,
            "suburb": record.suburb,
        }
        for record in records
    }
    tables: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] | None = None
    if dataset_name == "febrl4":
        # FEBRL4 là linkage giữa hai nguồn; chỉ xét cặp ở hai bảng khác nhau.
        if len(records) != 10_000:
            raise ValueError("FEBRL4 is expected to have 5,000 records per source")
        midpoint = len(records) // 2
        tables = (
            {record.source_id: rows[record.source_id] for record in records[:midpoint]},
            {record.source_id: rows[record.source_id] for record in records[midpoint:]},
        )

    ids_by_entity: dict[str, list[str]] = defaultdict(list)
    for source_id, entity_id in entity_by_id.items():
        ids_by_entity[entity_id].append(source_id)

    entities = list(ids_by_entity.values())
    # Xáo trộn theo thực thể, không theo bản ghi: một người không thể xuất hiện
    # đồng thời ở train và test. Seed cố định giúp tái lập benchmark.
    random.Random(seed).shuffle(entities)
    total_records = len(records)
    targets = (total_records * 0.6, total_records * 0.8)
    entity_split: dict[str, str] = {}
    split_sizes = [0, 0, 0]
    for entity_ids in entities:
        if split_sizes[0] < targets[0]:
            split_index = 0
        elif split_sizes[0] + split_sizes[1] < targets[1]:
            split_index = 1
        else:
            split_index = 2
        split_name = ("train", "validation", "test")[split_index]
        for source_id in entity_ids:
            entity_split[source_id] = split_name
        split_sizes[split_index] += len(entity_ids)

    output: dict[str, Partition] = {}
    for split_name in ("train", "validation", "test"):
        split_rows = {
            source_id: row
            for source_id, row in rows.items()
            if entity_split[source_id] == split_name
        }
        split_truth: set[Pair] = set()
        # Nhãn đúng gồm mọi cặp cùng thực thể trong split; với FEBRL4 chỉ lấy
        # các cặp nối giữa hai nguồn, không lấy cặp trong cùng một nguồn.
        for entity_ids in entities:
            members = [source_id for source_id in entity_ids if source_id in split_rows]
            if len(members) < 2:
                continue
            if tables is None:
                split_truth.update(pair_key(a, b) for a, b in combinations(members, 2))
            else:
                left_ids = [source_id for source_id in members if source_id in tables[0]]
                right_ids = [source_id for source_id in members if source_id in tables[1]]
                split_truth.update(pair_key(a, b) for a in left_ids for b in right_ids)

        if tables is None:
            left = split_rows
            right = None
        else:
            left = {key: row for key, row in split_rows.items() if key in tables[0]}
            right = {key: row for key, row in split_rows.items() if key in tables[1]}
        output[split_name] = Partition(split_rows, left, right, split_truth)

    return output


def pair_metrics(
    truth: set[Pair], candidates: set[Pair], predicted: set[Pair]
) -> dict[str, float | int]:
    # FN tính trên toàn bộ nhãn đúng, kể cả cặp đã bị blocking loại bỏ.
    true_positive = len(truth & predicted)
    false_positive = len(predicted - truth)
    false_negative = len(truth - predicted)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    candidate_recall = len(truth & candidates) / len(truth) if truth else 0.0
    return {
        "true_pairs": len(truth),
        "candidate_pairs": len(candidates),
        "predicted_pairs": len(predicted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "candidate_recall": candidate_recall,
    }


def choose_threshold(
    truth: set[Pair], scores: dict[Pair, float], candidates: set[Pair]
) -> tuple[float, dict[str, float | int]]:
    """Chọn ngưỡng trên validation: F1 cao nhất, rồi precision, rồi ngưỡng."""
    if not scores:
        threshold = 1.0
        return threshold, pair_metrics(truth, candidates, set())
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_threshold = ordered[0][1]
    best_metrics = pair_metrics(truth, candidates, set())
    true_positive = false_positive = 0
    offset = 0
    while offset < len(ordered):
        threshold = ordered[offset][1]
        end = offset
        # Xử lý cùng lúc các cặp có cùng điểm vì quy tắc dự đoán là >= ngưỡng.
        while end < len(ordered) and ordered[end][1] == threshold:
            if ordered[end][0] in truth:
                true_positive += 1
            else:
                false_positive += 1
            end += 1
        false_negative = len(truth) - true_positive
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = true_positive / len(truth) if truth else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics = {
            "true_pairs": len(truth),
            "candidate_pairs": len(candidates),
            "predicted_pairs": true_positive + false_positive,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "candidate_recall": len(truth & candidates) / len(truth) if truth else 0.0,
        }
        if (f1, precision, threshold) > (
            best_metrics["f1"], best_metrics["precision"], best_threshold
        ):
            best_threshold, best_metrics = threshold, metrics
        offset = end
    return best_threshold, best_metrics


def metrics_at_threshold(
    truth: set[Pair], scores: dict[Pair, float], candidates: set[Pair], threshold: float
) -> dict[str, float | int]:
    # Ngưỡng đã chọn ở validation được áp dụng nguyên vẹn cho test.
    predicted = {pair for pair, score in scores.items() if score >= threshold}
    return pair_metrics(truth, candidates, predicted)


def result_record(
    dataset: str,
    method: str,
    partitions: dict[str, Partition],
    threshold: float,
    validation_metrics: dict[str, float | int],
    test_metrics: dict[str, float | int],
    timings: dict[str, float],
    label_budget: int | None = None,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "method": method,
        "records": {name: len(part.rows) for name, part in partitions.items()},
        "label_budget_per_class": label_budget,
        "validation_threshold": threshold,
        "validation": validation_metrics,
        "test": test_metrics,
        "timings_seconds": timings,
    }
