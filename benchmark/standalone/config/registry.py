"""Small explicit registries and strict G0 experiment-config validation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConfigError(ValueError):
    """Raised when a config is malformed or names an unknown registry item."""


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    status: str


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, name: str, status: str = "registered; implementation pending") -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("registry name must be a non-empty string")
        if name in self._entries:
            raise ValueError(f"duplicate registry name: {name}")
        self._entries[name] = RegistryEntry(name=name, status=status)

    def require(self, name: str, kind: str) -> RegistryEntry:
        try:
            return self._entries[name]
        except (KeyError, TypeError):
            raise ConfigError(f"unknown {kind} name: {name!r}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def __getitem__(self, name: str) -> RegistryEntry:
        return self._entries[name]

    def items(self):
        return self._entries.items()


@dataclass(frozen=True)
class RegistryBundle:
    datasets: Registry
    systems: Registry
    experiments: Registry


DATASETS = Registry()
DATASETS.register("masterone_test", "adapter and audit available; train/dev and verified entity gold blocked")
SYSTEMS = Registry()
SYSTEMS.register("exact", "implemented B0 exact/rule pair baseline")
SYSTEMS.register("weighted", "implemented Weighted V1 reimplementation; no fitting or threshold tuning")
SYSTEMS.register("masterone", "registered; full MasterOne system implementation pending")
EXPERIMENTS = RegistryBundle(DATASETS, SYSTEMS, Registry())
STAGES = frozenset({
    "profiling", "normalization", "blocking", "comparison", "scoring",
    "routing", "clustering", "evaluation", "end_to_end",
})
# G0 defines empty parameter allowlists. A later implementation must extend these
# deliberately when it introduces a real, documented stage parameter.
STAGE_PARAMETERS = {stage: frozenset() for stage in STAGES}


def validate_experiment_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigError("config root must be a JSON object")
    allowed = {"stage", "system", "dataset", "experiment", "parameters"}
    unknown = set(config) - allowed
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(sorted(map(str, unknown)))}")
    stage = config.get("stage")
    if not isinstance(stage, str) or stage not in STAGES:
        raise ConfigError(f"unknown stage name: {stage!r}; allowed: {', '.join(sorted(STAGES))}")
    system = config.get("system")
    if not isinstance(system, str):
        raise ConfigError("config field 'system' must be a registered system name")
    SYSTEMS.require(system, "system")
    if "dataset" in config:
        dataset = config["dataset"]
        if not isinstance(dataset, str):
            raise ConfigError("config field 'dataset' must be a registered dataset name")
        DATASETS.require(dataset, "dataset")
    if "experiment" in config:
        experiment = config["experiment"]
        if not isinstance(experiment, str):
            raise ConfigError("config field 'experiment' must be a registered experiment name")
        EXPERIMENTS.experiments.require(experiment, "experiment")
    if "parameters" in config:
        parameters = config["parameters"]
        if not isinstance(parameters, dict):
            raise ConfigError("config field 'parameters' must be a JSON object")
        unknown_parameters = set(parameters) - STAGE_PARAMETERS[stage]
        if unknown_parameters:
            raise ConfigError(
                f"unknown parameter(s) for stage {stage!r}: "
                f"{', '.join(sorted(map(str, unknown_parameters)))}"
            )
    return config
