"""Dev-only Platt calibration; never silently treats a raw score as probability."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from standalone.contracts.core import DataSplit, Probability
from standalone.contracts.labels import PairLabel


class CalibrationBlockedError(ValueError):
    pass


@dataclass(frozen=True)
class CalibratedScore:
    raw_similarity: float | None
    fs_log_score: float | None
    probability: Probability


@dataclass(frozen=True)
class PlattCalibrator:
    slope: float
    intercept: float
    source: str = "dev"
    version: str = "platt-newton-v1"

    @classmethod
    def fit(cls, scores: Sequence[float], labels: Sequence[PairLabel], split: DataSplit,
            *, max_iter: int = 100, tolerance: float = 1e-8) -> "PlattCalibrator":
        if split is not DataSplit.DEV:
            raise CalibrationBlockedError("calibration may fit on DEV only")
        if len(scores) != len(labels) or not scores:
            raise CalibrationBlockedError("nonempty, aligned dev scores and labels are required")
        rows = [(float(s), 1.0 if y is PairLabel.POSITIVE else 0.0)
                for s, y in zip(scores, labels) if y is not PairLabel.UNKNOWN]
        if not rows or len({y for _, y in rows}) < 2 or any(not math.isfinite(s) for s, _ in rows):
            raise CalibrationBlockedError("dev calibration requires finite scores and both labeled classes")
        positives = sum(y for _, y in rows)
        negatives = len(rows) - positives
        a, b = 0.0, math.log((positives+1)/(negatives+1))
        for _ in range(max_iter):
            g_a = g_b = h_aa = h_ab = h_bb = 0.0
            for x, y in rows:
                z = max(-40.0, min(40.0, a*x+b)); p = 1/(1+math.exp(-z)); w = max(1e-12, p*(1-p))
                g_a += (p-y)*x; g_b += p-y
                h_aa += w*x*x; h_ab += w*x; h_bb += w
            # Small L2 damping stabilizes separable/small dev samples.
            ridge = 1e-4
            h_aa += ridge; h_bb += ridge
            g_a += ridge*a; g_b += ridge*b
            det = h_aa*h_bb-h_ab*h_ab
            if det <= 1e-18: raise CalibrationBlockedError("dev scores do not identify a stable Platt fit")
            da = (h_bb*g_a-h_ab*g_b)/det; db = (h_aa*g_b-h_ab*g_a)/det
            a -= da; b -= db
            if max(abs(da), abs(db)) < tolerance: break
        if not math.isfinite(a) or not math.isfinite(b):
            raise CalibrationBlockedError("Platt fit became non-finite")
        return cls(a, b)

    def predict(self, *, raw_similarity: float | None = None,
                fs_log_score: float | None = None, score: float) -> CalibratedScore:
        z = max(-40.0, min(40.0, self.slope*score+self.intercept))
        probability = 1/(1+math.exp(-z))
        return CalibratedScore(raw_similarity, fs_log_score, Probability(probability))
