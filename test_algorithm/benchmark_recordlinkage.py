"""RecordLinkage Toolkit benchmarks: unsupervised ECM and supervised logistic regression."""

from __future__ import annotations

import random
from time import perf_counter
from typing import Any

import jellyfish
import numpy as np
import pandas as pd
import recordlinkage

from test_algorithm.benchmark_common import (
    Partition,
    choose_threshold,
    metrics_at_threshold,
    pair_key,
    result_record,
)


def make_index(partition: Partition) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.MultiIndex]:
    left = pd.DataFrame.from_dict(partition.left, orient="index")
    left.index.name = "record_id"
    right = None
    if partition.right is not None:
        right = pd.DataFrame.from_dict(partition.right, orient="index")
        right.index.name = "record_id"

    indexer = recordlinkage.Index()
    # RecordLinkage hợp (OR) ba luật blocking. Sorted neighbourhood giữ thêm
    # các họ gần nhau trong thứ tự sắp xếp, nên tập ứng viên khác Splink.
    indexer.block("surname")
    indexer.block("date_of_birth")
    indexer.sortedneighbourhood("surname", window=5)
    candidate_index = (
        indexer.index(left, right) if right is not None else indexer.index(left)
    )
    return left, right, candidate_index


def canonical_candidates(candidate_index: pd.MultiIndex) -> set[tuple[str, str]]:
    return {pair_key(left, right) for left, right in candidate_index}


def compare_candidates(
    candidate_index: pd.MultiIndex,
    left: pd.DataFrame,
    right: pd.DataFrame | None,
) -> pd.DataFrame:
    # Mỗi cặp ứng viên trở thành một hàng gồm 5 đặc trưng so sánh.
    compare = recordlinkage.Compare(n_jobs=1)

    def jaro_winkler(left_values, right_values):
        return np.asarray(
            [
                np.nan
                if pd.isna(left) or pd.isna(right)
                else jellyfish.jaro_winkler_similarity(str(left), str(right))
                for left, right in zip(left_values, right_values)
            ],
            dtype=float,
        )

    def normalized_levenshtein(left_values, right_values):
        similarities = []
        for left, right in zip(left_values, right_values):
            if pd.isna(left) or pd.isna(right):
                similarities.append(np.nan)
                continue
            left, right = str(left), str(right)
            longest = max(len(left), len(right))
            # 1 nghĩa là giống hệt; 0 là khác hoàn toàn theo độ dài chuỗi.
            similarities.append(
                1.0
                if longest == 0
                else 1.0 - jellyfish.levenshtein_distance(left, right) / longest
            )
        return np.asarray(similarities, dtype=float)

    compare.compare_vectorized(
        jaro_winkler, "given_name", "given_name", label="given_name"
    )
    compare.compare_vectorized(jaro_winkler, "surname", "surname", label="surname")
    compare.compare_vectorized(
        normalized_levenshtein,
        "date_of_birth",
        "date_of_birth",
        label="date_of_birth",
    )
    compare.exact("postcode", "postcode", label="postcode")
    compare.exact("suburb", "suburb", label="suburb")
    # Điền 0.5 cho đặc trưng còn NaN. Với ECM, bước binarize=0.85 sau đó sẽ
    # xem 0.5 là 0; với Logistic, 0.5 vẫn là giá trị liên tục.
    return compare.compute(candidate_index, left, right).fillna(0.5)


def sample_labeled_candidates(
    candidate_index: pd.MultiIndex,
    true_pairs: set[tuple[str, str]],
    budget_per_class: int,
    seed: int = 42,
) -> tuple[pd.MultiIndex, pd.MultiIndex]:
    indexed_pairs = [tuple(map(str, pair)) for pair in candidate_index]
    # Chỉ lấy nhãn từ ứng viên thuộc train; nhãn validation/test không tham gia học.
    positives = [pair for pair in indexed_pairs if pair_key(*pair) in true_pairs]
    negatives = [pair for pair in indexed_pairs if pair_key(*pair) not in true_pairs]
    rng = random.Random(seed)
    # Giới hạn số cặp mỗi lớp để mô hình có dữ liệu match và non-match cân bằng.
    positive_count = min(budget_per_class, len(positives))
    negative_count = min(budget_per_class, len(negatives))
    if positive_count < 2 or negative_count < 2:
        raise ValueError(
            "The training split needs at least two candidate matches and non-matches; "
            "check the blocker or label budget."
        )
    selected_positives = rng.sample(positives, positive_count)
    selected = selected_positives + rng.sample(negatives, negative_count)
    selected_index = pd.MultiIndex.from_tuples(selected, names=candidate_index.names)
    match_index = pd.MultiIndex.from_tuples(
        selected_positives, names=candidate_index.names
    )
    return selected_index, match_index


def fit_supervised(
    train: Partition, budget_per_class: int
) -> tuple[recordlinkage.LogisticRegressionClassifier, int, int]:
    left, right, candidate_index = make_index(train)
    train_index, match_index = sample_labeled_candidates(
        candidate_index, train.true_pairs, budget_per_class
    )
    features = compare_candidates(train_index, left, right)
    classifier = recordlinkage.LogisticRegressionClassifier(n_jobs=1)
    # RecordLinkage 0.14 dùng ``&`` trên hai pandas Index trong ``fit``;
    # thao tác này lỗi khi chúng khác kích thước. Tạo nhãn 0/1 theo đúng thứ tự
    # hàng đặc trưng rồi gọi adapter nội bộ để huấn luyện logistic kernel.
    positive_pairs = {pair_key(*pair) for pair in match_index}
    labels = np.asarray(
        [int(pair_key(*pair) in positive_pairs) for pair in features.index],
        dtype=np.int8,
    )
    classifier._fit(features.to_numpy(), labels)
    return classifier, len(match_index), len(train_index) - len(match_index)


def fit_ecm(train: Partition) -> recordlinkage.ECMClassifier:
    left, right, candidate_index = make_index(train)
    features = compare_candidates(candidate_index, left, right)
    # ECM ước lượng hai nhóm match/non-match từ đặc trưng, không dùng nhãn thật.
    # binarize biến mức giống >= 0.85 thành 1 trước khi học.
    classifier = recordlinkage.ECMClassifier(
        init="jaro", binarize=0.85, max_iter=100
    )
    classifier.fit_predict(features)
    return classifier


def predict(
    classifier: Any, partition: Partition
) -> tuple[dict[tuple[str, str], float], set[tuple[str, str]]]:
    left, right, candidate_index = make_index(partition)
    candidates = canonical_candidates(candidate_index)
    features = compare_candidates(candidate_index, left, right)
    # Cả ECM và Logistic trả xác suất cho mọi cặp còn lại sau blocking.
    probabilities = classifier.prob(features)
    scores = {
        pair_key(*pair): float(probability)
        for pair, probability in zip(features.index, probabilities)
    }
    return scores, candidates


def benchmark(
    dataset: str, method: str, partitions: dict[str, Partition], label_budget: int
) -> dict[str, Any]:
    started = perf_counter()
    train_started = perf_counter()
    if method == "recordlinkage_ecm":
        classifier = fit_ecm(partitions["train"])
        label_budget_used = None
    elif method == "recordlinkage_logistic":
        classifier, positive_labels, negative_labels = fit_supervised(
            partitions["train"], label_budget
        )
        label_budget_used = min(positive_labels, negative_labels)
    else:
        raise ValueError(f"Unknown RecordLinkage method: {method}")
    train_seconds = perf_counter() - train_started

    # Cùng một cách chọn ngưỡng và đo test như Splink; chỉ mô hình học khác.
    validation_scores, validation_candidates = predict(classifier, partitions["validation"])
    threshold, validation_metrics = choose_threshold(
        partitions["validation"].true_pairs, validation_scores, validation_candidates
    )
    test_started = perf_counter()
    test_scores, test_candidates = predict(classifier, partitions["test"])
    test_metrics = metrics_at_threshold(
        partitions["test"].true_pairs, test_scores, test_candidates, threshold
    )
    predict_seconds = perf_counter() - test_started
    return result_record(
        dataset,
        method,
        partitions,
        threshold,
        validation_metrics,
        test_metrics,
        {
            "train": round(train_seconds, 3),
            "test_prediction": round(predict_seconds, 3),
            "total": round(perf_counter() - started, 3),
        },
        label_budget_used,
    )
