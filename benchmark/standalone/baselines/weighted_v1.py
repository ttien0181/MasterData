"""Weighted V1 reimplementation using standard-library similarities only."""
from __future__ import annotations

import re
import unicodedata

from standalone.contracts.core import Decision, Record, Similarity


WEIGHTS = {"name": .35, "phone": .25, "email": .25, "address": .15}
AUTO_MATCH_THRESHOLD = .82
NO_MATCH_THRESHOLD = .35


def _norm(field: str, value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    if field in {"phone", "email"}:
        return "".join(re.findall(r"\d", value)) if field == "phone" else value
    return " ".join(value.split())


def _trigrams(text: str) -> set[str]:
    padded = f"  {text}  "
    return {padded[i:i + 3] for i in range(max(0, len(padded) - 2))}


def field_similarity(field: str, left: str | None, right: str | None) -> float | None:
    """Missing either side is omitted; email/phone are exact, text uses Dice trigrams."""
    a, b = _norm(field, left), _norm(field, right)
    if not a or not b:
        return None
    if field in {"phone", "email"}:
        return float(a == b)
    ga, gb = _trigrams(a), _trigrams(b)
    return 2 * len(ga & gb) / (len(ga) + len(gb)) if ga or gb else 1.0


class WeightedV1Reimplementation:
    """Predeclared illustrative thresholds; no fit or test-set tuning is performed."""
    name = "weighted"
    version = "Weighted V1 reimplementation v1.0"

    def score(self, left: Record, right: Record) -> tuple[Similarity, tuple[str, ...]]:
        components: dict[str, float] = {}
        for field, weight in WEIGHTS.items():
            value = field_similarity(field, left.fields.get(field), right.fields.get(field))
            if value is not None:
                components[field] = value * weight
        used_weight = sum(WEIGHTS[field] for field in components)
        score = sum(components.values()) / used_weight if used_weight else 0.0
        evidence = tuple(f"{field}_similarity={components[field] / WEIGHTS[field]:.6f}"
                         for field in WEIGHTS if field in components)
        evidence += (f"weighted_similarity={score:.6f}", self.version)
        return Similarity(score), evidence

    @staticmethod
    def route(score: Similarity, evidence: tuple[str, ...]) -> Decision:
        if not any(any(item.startswith(f"{field}_similarity=") for field in WEIGHTS)
                   for item in evidence):
            return Decision.REVIEW
        if score.value >= AUTO_MATCH_THRESHOLD:
            return Decision.AUTO_MATCH
        if score.value <= NO_MATCH_THRESHOLD:
            return Decision.NO_MATCH
        return Decision.REVIEW
