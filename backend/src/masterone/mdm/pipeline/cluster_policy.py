"""Versioned domain/source policy and explicit rollback preset."""
from dataclasses import dataclass, replace
from pathlib import Path

import yaml


def enabled():
    from masterone.mdm.engine_adapter import feature
    return feature("cluster_safety_v2_enabled")


@dataclass(frozen=True)
class ClusterPolicy:
    version: str = "p119_cluster_person_v2"
    max_cluster_size: int = 20
    max_pair_checks_per_union: int = 190
    max_pair_checks_per_run: int = 1000000
    max_reviewed_cluster_size: int = 100
    max_reviewed_pair_checks_per_union: int = 4950
    unique_sources: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_priority: tuple[tuple[str, int], ...] = (("CRM", 0), ("ERP", 1), ("BILL", 2))
    survivorship_version: str = "p119_golden_v3"
    field_source_priority: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()

    def __post_init__(self):
        if min(self.max_cluster_size, self.max_pair_checks_per_union, self.max_pair_checks_per_run) < 1:
            raise ValueError("Cluster limits must be positive")
        if self.max_cluster_size > 100:
            raise ValueError("Cluster size exceeds supported guard bound")
        if not self.max_cluster_size <= self.max_reviewed_cluster_size <= 100:
            raise ValueError("Reviewed cluster size exceeds supported guard bound")
        if self.max_reviewed_pair_checks_per_union < self.max_reviewed_cluster_size * (self.max_reviewed_cluster_size - 1) // 2:
            raise ValueError("Reviewed guard budget must cover every member pair")

    def unique(self, source, domain):
        return domain in dict(self.unique_sources).get(source, ())


def load_policy(*, legacy=False):
    path = Path(__file__).resolve().parents[1] / "config" / "clustering_person_v2.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = raw["legacy_unique_sources" if legacy else "unique_sources"]
    return ClusterPolicy(version=raw["version"], max_cluster_size=raw["max_cluster_size"],
        max_pair_checks_per_union=raw["max_pair_checks_per_union"],
        max_pair_checks_per_run=raw["max_pair_checks_per_run"],
        max_reviewed_cluster_size=raw.get("max_reviewed_cluster_size", 100),
        max_reviewed_pair_checks_per_union=raw.get("max_reviewed_pair_checks_per_union", 4950),
        unique_sources=tuple(sorted((s, tuple(domains)) for s, domains in sources.items())),
        source_priority=tuple(sorted(raw["source_priority"].items())),
        survivorship_version=raw.get("survivorship_version", "p119_golden_v3"),
        field_source_priority=tuple(sorted((field, tuple(sorted(priorities.items())))
                                          for field, priorities in raw.get("field_source_priority", {}).items())))


def reviewed_policy(policy):
    """Explicit exact-group review changes bounds, preserving all hard guards."""
    return replace(policy, max_cluster_size=policy.max_reviewed_cluster_size,
                   max_pair_checks_per_union=policy.max_reviewed_pair_checks_per_union)


@dataclass
class GuardBudget:
    checked: int = 0
    visited: int = 0
    cache_hits: int = 0
    blocked: int = 0
