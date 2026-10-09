"""Optional Splink adapter. Splink predicts its own blocked universe, not fixed pairs."""
from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass
from typing import Mapping

from standalone.contracts.core import (Decision, LogLikelihoodRatio, Pair, PairPrediction,
                                       Probability, global_record_id)


class OptionalDependencyUnavailable(RuntimeError):
    pass


class UnsupportedSplinkCapability(RuntimeError):
    pass


@dataclass(frozen=True)
class SplinkCapabilities:
    native_blocking: bool = True
    fixed_pair_scoring: bool = False
    native_clustering: bool = True
    required_version: str = "5.0.0"


class SplinkAdapter:
    name = "splink"
    version = "Splink 5 adapter"
    capabilities = SplinkCapabilities()

    @staticmethod
    def availability() -> tuple[bool, str]:
        if importlib.util.find_spec("splink") is None:
            return False, "optional dependency splink==5.0.0 is not installed"
        try:
            version = importlib.metadata.version("splink")
        except importlib.metadata.PackageNotFoundError:
            return False, "splink module exists but distribution metadata is unavailable"
        if version.split(".")[0] != "5":
            return False, f"installed Splink {version}; adapter requires major version 5"
        return True, f"installed Splink {version}"

    def predict_fixed_pairs(self, *_args, **_kwargs):
        raise UnsupportedSplinkCapability(
            "Splink predict generates candidates from native blocking rules; fixed-pair scoring is unsupported"
        )

    def predict_native(self, linker, *, auto_match_threshold: float,
                       no_match_threshold: float, prediction_threshold: float | None = None):
        """Run Splink's native blocking/prediction and map each returned native pair.

        The caller constructs a configured Splink 5 Linker and blocking rules. This
        method deliberately cannot score a benchmark-supplied fixed candidate list.
        """
        self.require_available()
        if no_match_threshold > auto_match_threshold:
            raise ValueError("threshold order invalid")
        kwargs = {}
        if prediction_threshold is not None:
            kwargs["threshold_match_probability"] = prediction_threshold
        result = linker.inference.predict(**kwargs)
        frame = result.as_pandas_dataframe()
        predictions = []
        for row in frame.to_dict(orient="records"):
            left_source = str(row.get("source_dataset_l", ""))
            right_source = str(row.get("source_dataset_r", ""))
            left_id = global_record_id("splink", left_source, str(row["unique_id_l"]))
            right_id = global_record_id("splink", right_source, str(row["unique_id_r"]))
            pair = Pair.create(left_id, right_id, left_source, right_source)
            predictions.append(self.normalize_native_prediction(
                row, pair_id=pair.pair_id, auto_match_threshold=auto_match_threshold,
                no_match_threshold=no_match_threshold,
            ))
        return tuple(predictions)

    @staticmethod
    def normalize_native_prediction(row: Mapping[str, object], *, pair_id: str,
                                    auto_match_threshold: float,
                                    no_match_threshold: float) -> PairPrediction:
        """Map documented Splink match_weight/probability into typed benchmark scores."""
        raw_probability = row.get("match_probability")
        raw_weight = row.get("match_weight")
        if raw_probability is None or raw_weight is None:
            raise ValueError("Splink prediction row requires match_probability and match_weight")
        probability = Probability(float(raw_probability))
        weight = LogLikelihoodRatio(float(raw_weight))
        if no_match_threshold > auto_match_threshold:
            raise ValueError("threshold order invalid")
        decision = (Decision.AUTO_MATCH if probability.value >= auto_match_threshold else
                    Decision.NO_MATCH if probability.value <= no_match_threshold else Decision.REVIEW)
        return PairPrediction(pair_id, probability, decision,
                              (f"splink_match_weight={weight.value:.8g}", "splink_native_blocking"))

    def require_available(self) -> None:
        available, reason = self.availability()
        if not available:
            raise OptionalDependencyUnavailable(reason)
        # Import is deliberately delayed until the adapter is invoked.
        import splink  # type: ignore[import-not-found]  # noqa: F401
