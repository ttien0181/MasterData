"""Small terminal runner for the implemented pair track and basic baselines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Mapping

from standalone.baselines.exact import ExactRuleBaseline
from standalone.baselines.weighted_v1 import WeightedV1Reimplementation
from standalone.contracts.core import DatasetBundle, PairPrediction, Score
from standalone.contracts.labels import PairLabel
from standalone.datasets.masterone import MasterOneAdapter
from standalone.datasets.universe import LegalPairUniverse
from standalone.evaluation.metrics import MetricValue, pair_metrics
from standalone.runtime.resources import ResourceTracker


SYSTEMS = {"exact": ExactRuleBaseline, "weighted": WeightedV1Reimplementation}


@dataclass(frozen=True)
class SystemRun:
    system: str
    version: str
    metrics: Mapping[str, MetricValue]
    prediction_count: int
    runtime: Mapping[str, object]


@dataclass(frozen=True)
class BenchmarkRun:
    dataset_id: str
    dataset_version: str
    track: str
    split: str
    labeled_pair_count: int
    positive_count: int
    negative_count: int
    unknown_count: int
    legal_pair_universe_count: int
    gold_scope: str
    protocol_note: str
    load_seconds: float
    end_to_end_seconds: float
    runs: tuple[SystemRun, ...]


def load_masterone() -> DatasetBundle:
    root = Path(__file__).resolve().parents[2]
    return MasterOneAdapter(root / "datasets" / "masterone_test").load()


def run_pair_baselines(bundle: DatasetBundle, names: tuple[str, ...]) -> BenchmarkRun:
    started = perf_counter()
    if not names:
        raise ValueError("at least one baseline system is required")
    unknown = set(names) - set(SYSTEMS)
    if unknown:
        raise ValueError(f"unsupported system(s): {', '.join(sorted(unknown))}; available: {', '.join(SYSTEMS)}")
    records = {row.record_id: row for row in bundle.records}
    labels = {row.pair.pair_id: row.label for row in bundle.pairs}
    results: list[SystemRun] = []
    for name in names:
        tracker = ResourceTracker()
        baseline = SYSTEMS[name]()

        def score_all() -> dict[str, tuple[Score, tuple[str, ...]]]:
            output: dict[str, tuple[Score, tuple[str, ...]]] = {}
            for row in bundle.pairs:
                pair = row.pair
                output[pair.pair_id] = baseline.score(
                    records[pair.left_record_id], records[pair.right_record_id])
            return output

        scored = tracker.measure("score", score_all, item_count=len(bundle.pairs))

        def route_all() -> dict[str, PairPrediction]:
            return {pair_id: PairPrediction(pair_id, score,
                                            baseline.route(score, evidence), evidence)
                    for pair_id, (score, evidence) in scored.items()}

        predictions = tracker.measure("route", route_all, item_count=len(bundle.pairs))
        metrics = tracker.measure("evaluate", lambda: pair_metrics(
            labels, predictions, coverage=bundle.gold_coverage))
        results.append(SystemRun(name, baseline.version, metrics, len(predictions), tracker.snapshot()))
    positive = sum(label is PairLabel.POSITIVE for label in labels.values())
    negative = sum(label is PairLabel.NEGATIVE for label in labels.values())
    unknown = sum(label is PairLabel.UNKNOWN for label in labels.values())
    return BenchmarkRun(
        dataset_id=bundle.metadata.dataset_id, dataset_version=bundle.metadata.version,
        track="pairs", split="test", labeled_pair_count=positive + negative,
        positive_count=positive, negative_count=negative, unknown_count=unknown,
        legal_pair_universe_count=LegalPairUniverse.from_records(bundle.records, bundle.metadata).pair_count,
        gold_scope=bundle.gold_coverage.pair_labels.value if bundle.gold_coverage.pair_labels else "none",
        protocol_note=("Descriptive internal test run only. All available labels are test; prior test use is "
                       "unknown. No tuning or threshold selection was performed."),
        load_seconds=0.0,
        end_to_end_seconds=perf_counter() - started,
        runs=tuple(results),
    )


def run_masterone_pair_baselines(names: tuple[str, ...]) -> BenchmarkRun:
    """Measure adapter load plus the supported pair-track execution."""
    started = perf_counter()
    load_started = perf_counter()
    bundle = load_masterone()
    load_seconds = perf_counter() - load_started
    result = run_pair_baselines(bundle, names)
    return BenchmarkRun(
        dataset_id=result.dataset_id, dataset_version=result.dataset_version, track=result.track,
        split=result.split, labeled_pair_count=result.labeled_pair_count,
        positive_count=result.positive_count, negative_count=result.negative_count,
        unknown_count=result.unknown_count, legal_pair_universe_count=result.legal_pair_universe_count,
        gold_scope=result.gold_scope, protocol_note=result.protocol_note,
        load_seconds=load_seconds, end_to_end_seconds=perf_counter() - started, runs=result.runs,
    )


def format_terminal_report(result: BenchmarkRun) -> str:
    lines = [
        f"Dataset: {result.dataset_id}@{result.dataset_version} | track={result.track} | split={result.split}",
        f"Labeled pairs: {result.labeled_pair_count} (positive={result.positive_count}, negative={result.negative_count}, "
        f"unknown={result.unknown_count}) | legal pair universe={result.legal_pair_universe_count}",
        f"Gold coverage: {result.gold_scope}",
        f"Load seconds: {result.load_seconds:.6f} | end-to-end seconds (load + systems + evaluation): "
        f"{result.end_to_end_seconds:.6f}",
        result.protocol_note,
        "",
        "System | Precision | Recall | F1 | Auto precision | Review rate | Score+route sec | Peak RSS bytes",
    ]
    for run in result.runs:
        m = run.metrics
        stages = run.runtime["stages"]  # type: ignore[assignment]
        timing = stages["score"]["wall_seconds"] + stages["route"]["wall_seconds"]  # type: ignore[index]
        def display(value: MetricValue) -> str:
            if value.value is not None:
                return f"{value.value:.4f}" if isinstance(value.value, float) else str(value.value)
            return f"N/A ({value.reason})"
        rss = run.runtime["process_peak_rss_bytes"]
        lines.append(" | ".join((run.system, display(m["precision"]), display(m["recall"]), display(m["f1"]),
                                 display(m["auto_match_precision"]), display(m["review_rate"]),
                                 f"{timing:.6f}", str(rss) if rss is not None else "N/A (unavailable)")))
        lines.append(f"  version={run.version}; average_precision={display(m['average_precision'])}; "
                     f"Brier={display(m['brier_score'])}; runtime stages={list(run.runtime['stages'])}")
    return "\n".join(lines)
