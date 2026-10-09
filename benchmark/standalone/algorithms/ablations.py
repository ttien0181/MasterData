"""Predeclared ablation switches; availability follows evidence and data fields."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ablation:
    code: str
    name: str
    weighted: bool = False
    fellegi_sunter: bool = False
    term_frequency: bool = False
    hub_adjustment: bool = False
    quality: bool = False
    vietnamese_normalization: bool = False
    temporal: bool = False
    available: bool = True
    reason: str = ""


def ablation_catalog(*, temporal_fields_available: bool = False) -> tuple[Ablation, ...]:
    return (
        Ablation("A0", "weighted", weighted=True),
        Ablation("A1", "Fellegi–Sunter", fellegi_sunter=True),
        Ablation("A2", "Fellegi–Sunter + term frequency", fellegi_sunter=True, term_frequency=True),
        Ablation("A3", "Fellegi–Sunter + term frequency + hub", fellegi_sunter=True,
                 term_frequency=True, hub_adjustment=True),
        Ablation("A4", "A3 + quality evidence", fellegi_sunter=True, term_frequency=True,
                 hub_adjustment=True, quality=True),
        Ablation("A5", "A4 + Vietnamese normalization", fellegi_sunter=True, term_frequency=True,
                 hub_adjustment=True, quality=True, vietnamese_normalization=True),
        Ablation("A6", "A5 + temporal evidence", fellegi_sunter=True, term_frequency=True,
                 hub_adjustment=True, quality=True, vietnamese_normalization=True, temporal=True,
                 available=temporal_fields_available,
                 reason="" if temporal_fields_available else "no validated business timestamp fields"),
    )
