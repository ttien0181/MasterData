"""Run the shared holdout benchmark for Splink and RecordLinkage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != SCRIPT_DIR]
sys.path.insert(0, str(REPO_ROOT))

from data import DatasetFactory  # noqa: E402
from test_algorithm.benchmark_common import load_splits  # noqa: E402
from test_algorithm.benchmark_recordlinkage import benchmark as run_recordlinkage  # noqa: E402
from test_algorithm.benchmark_splink import benchmark as run_splink  # noqa: E402


def print_result(result: dict[str, Any]) -> None:
    test = result["test"]
    val = result["validation"]
    print(
        f"  {result['method']}: F1={test['f1']:.4f}, "
        f"precision={test['precision']:.4f}, recall={test['recall']:.4f}, "
        f"blocking recall={test['candidate_recall']:.4f}, "
        f"threshold={result['validation_threshold']:.4f} "
        f"(validation F1={val['f1']:.4f})"
    )
    print(
        f"    pairs: candidates={test['candidate_pairs']}, "
        f"predicted={test['predicted_pairs']}, TP={test['true_positive']}, "
        f"FP={test['false_positive']}, FN={test['false_negative']}"
    )
    times = result["timings_seconds"]
    print(
        f"    time: train={times['train']:.2f}s, "
        f"test prediction={times['test_prediction']:.2f}s, total={times['total']:.2f}s"
    )
    if result["label_budget_per_class"] is not None:
        print(
            "    training labels: "
            f"{result['label_budget_per_class']} match and "
            f"{result['label_budget_per_class']} non-match pairs"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=[*DatasetFactory.list_available_datasets(), "all"],
        default="all",
    )
    parser.add_argument("--label-budget", type=int, default=1000)
    parser.add_argument("--json", type=Path, help="Save detailed results as JSON")
    args = parser.parse_args()
    if args.label_budget < 2:
        parser.error("--label-budget must be at least 2")

    datasets = (
        DatasetFactory.list_available_datasets()
        if args.dataset == "all"
        else [args.dataset]
    )
    results = []
    for dataset in datasets:
        print(f"\nBenchmarking {dataset} with entity-disjoint 60/20/20 splits")
        # Tạo split một lần để cả ba cấu hình dùng đúng cùng train/validation/test.
        partitions = load_splits(dataset)
        for name, partition in partitions.items():
            print(
                f"  {name}: {len(partition.rows)} records, "
                f"{len(partition.true_pairs)} true pairs"
            )

        # Splink và RecordLinkage có luật blocking riêng; candidate_pairs phản
        # ánh cả blocking lẫn mô hình, không chỉ thuật toán phân loại.
        dataset_results = [run_splink(dataset, partitions)]
        dataset_results.append(
            run_recordlinkage(dataset, "recordlinkage_ecm", partitions, args.label_budget)
        )
        dataset_results.append(
            run_recordlinkage(
                dataset, "recordlinkage_logistic", partitions, args.label_budget
            )
        )
        for result in dataset_results:
            print_result(result)
        results.extend(dataset_results)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nSaved {args.json}")


if __name__ == "__main__":
    main()
