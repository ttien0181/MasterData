"""Portable run metadata contract; collecting metadata does not run an experiment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any


def config_hash(config: Any) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RuntimeMetadata:
    seed: int | None
    platform: str
    python_version: str
    package_versions: dict[str, str | None]
    device: str
    config_hash: str
    timing_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def collect_metadata(config: Any, *, seed: int | None = None, device: str = "cpu",
                     packages: tuple[str, ...] = ()) -> RuntimeMetadata:
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return RuntimeMetadata(seed, platform.platform(), platform.python_version(), versions,
                           device, config_hash(config))


class Timer:
    def __enter__(self) -> "Timer":
        self._started = time.perf_counter()
        self.elapsed_seconds: float | None = None
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_seconds = time.perf_counter() - self._started
