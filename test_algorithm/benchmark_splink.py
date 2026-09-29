"""Splink 5 benchmark using EM trained on the training split."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != SCRIPT_DIR]
sys.path.insert(0, str(REPO_ROOT))

import splink.comparison_library as cl  # noqa: E402
from splink import DuckDBAPI, Linker, SettingsCreator, block_on  # noqa: E402

from data import DatasetFactory  # noqa: E402
from test_algorithm.benchmark_common import (  # noqa: E402
    Partition,
    choose_threshold,
    load_splits,
    metrics_at_threshold,
    pair_key,
    result_record,
)


def register_partition(db_api: DuckDBAPI, partition: Partition, label: str):
    tables = [partition.left]
    if partition.right is not None:
        tables.append(partition.right)
    return [
        db_api.register(
            [dict(row) for row in table.values()],
            dataset_display_name=f"{label}_{index + 1}",
        )
        for index, table in enumerate(tables)
    ]


def build_linker(partition: Partition, settings: Any, label: str) -> Linker:
    db_api = DuckDBAPI()
    registered = register_partition(db_api, partition, label)
    # FEBRL4 ghép hai bảng (link_only); FEBRL3 tìm bản ghi trùng trong một bảng.
    return Linker(
        registered if partition.is_linkage else registered[0],
        settings,
        log_level="WARNING",
    )


def train(partition: Partition) -> tuple[dict[str, Any], float]:
    started = perf_counter()
    settings = SettingsCreator(
        link_type="link_only" if partition.is_linkage else "dedupe_only",
        # Mỗi comparison biến mức độ giống nhau của một trường thành bằng chứng
        # cho hoặc chống lại giả thuyết hai bản ghi thuộc cùng một người.
        comparisons=[
            cl.NameComparison("given_name"),
            cl.NameComparison("surname"),
            cl.DateOfBirthComparison(
                "date_of_birth", input_is_string=True, datetime_format="%Y%m%d"
            ),
            cl.ExactMatch("postcode"),
            cl.ExactMatch("suburb"),
        ],
        # Các luật được hợp (OR) để tạo ứng viên lúc predict. Cặp không qua
        # luật nào sẽ không được chấm điểm, dù các trường khác rất giống nhau.
        blocking_rules_to_generate_predictions=[
            block_on("surname"),
            block_on("date_of_birth"),
            block_on("given_name", "postcode"),
        ],
    )
    linker = build_linker(partition, settings, "train")
    # u: tần suất trùng khớp ngẫu nhiên giữa hai bản ghi không liên quan.
    linker.training.estimate_u_using_random_sampling(max_pairs=250_000, seed=42)
    # EM học tham số từ hai nhóm cặp được tạo bằng các luật blocking riêng;
    # không dùng nhãn match/non-match của ground truth để huấn luyện Splink.
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("given_name", "surname"), record_sample_proportion=1.0
    )
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("date_of_birth"), record_sample_proportion=1.0
    )
    # Lưu tham số đã học để validation và test chỉ dùng mô hình train.
    trained_settings = linker.misc.save_model_to_json()
    return trained_settings, perf_counter() - started


def predict(
    partition: Partition, settings: dict[str, Any], label: str
) -> tuple[dict[tuple[str, str], float], set[tuple[str, str]]]:
    linker = build_linker(partition, settings, label)
    predictions = linker.inference.predict()
    records = predictions.as_record_list()
    scores = {
        pair_key(row["unique_id_l"], row["unique_id_r"]): float(
            row["match_probability"]
        )
        for row in records
    }
    # Mỗi dòng predict là một cặp ứng viên; số ứng viên là số khóa trong scores.
    return scores, set(scores)


def benchmark(dataset: str, partitions: dict[str, Partition]) -> dict[str, Any]:
    started = perf_counter()
    settings, train_seconds = train(partitions["train"])
    validation_scores, validation_candidates = predict(
        partitions["validation"], settings, "validation"
    )
    # Chỉ validation được dùng để chọn ngưỡng tối đa hóa F1.
    threshold, validation_metrics = choose_threshold(
        partitions["validation"].true_pairs,
        validation_scores,
        validation_candidates,
    )
    test_started = perf_counter()
    test_scores, test_candidates = predict(partitions["test"], settings, "test")
    # Test chỉ dùng để đánh giá với ngưỡng đã cố định ở trên.
    test_metrics = metrics_at_threshold(
        partitions["test"].true_pairs, test_scores, test_candidates, threshold
    )
    return result_record(
        dataset,
        "splink_em",
        partitions,
        threshold,
        validation_metrics,
        test_metrics,
        {
            "train": round(train_seconds, 3),
            "test_prediction": round(perf_counter() - test_started, 3),
            "total": round(perf_counter() - started, 3),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=[*DatasetFactory.list_available_datasets(), "all"],
        default="all",
    )
    parser.add_argument("--json", type=Path, help="Save detailed results as JSON")
    args = parser.parse_args()
    datasets = (
        DatasetFactory.list_available_datasets()
        if args.dataset == "all"
        else [args.dataset]
    )
    results = []
    for dataset in datasets:
        partitions = load_splits(dataset)
        result = benchmark(dataset, partitions)
        results.append(result)
        test = result["test"]
        print(
            f"{dataset}: F1={test['f1']:.4f}, precision={test['precision']:.4f}, "
            f"recall={test['recall']:.4f}, blocking recall={test['candidate_recall']:.4f}, "
            f"threshold={result['validation_threshold']:.4f}"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Saved {args.json}")


if __name__ == "__main__":
    main()
