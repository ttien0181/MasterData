"""Generic stage registry; algorithms remain outside this module."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from masterone.storage.manifest import StageManifest


@dataclass(frozen=True)
class StageDefinition:
    name: str
    input_uri: str
    output_uri: str
    schema: dict[str, str]
    fingerprint: str
    config_hash: str
    code_version: str


class StageRegistry:
    """Register stage contracts and decide whether a checkpoint is reusable."""

    def __init__(self, manifest_exists: Callable[[str], bool] | None = None):
        self._definitions: dict[str, StageDefinition] = {}
        self._manifest_exists = manifest_exists or (lambda _uri: False)

    @staticmethod
    def config_hash(config: dict[str, Any]) -> str:
        encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def register(self, definition: StageDefinition) -> StageDefinition:
        if definition.name in self._definitions:
            raise ValueError(f"Stage already registered: {definition.name}")
        self._definitions[definition.name] = definition
        return definition

    def get(self, stage: str) -> StageDefinition:
        try:
            return self._definitions[stage]
        except KeyError as exc:
            raise KeyError(f"Unknown stage: {stage}") from exc

    def can_resume(self, stage: str, manifest: StageManifest | None) -> bool:
        definition = self.get(stage)
        return bool(
            manifest
            and self._manifest_exists(manifest.output_uri)
            and manifest.input_fingerprint == definition.fingerprint
            and manifest.config_hash == definition.config_hash
            and manifest.code_version == definition.code_version
            and manifest.schema == definition.schema
        )

    def definitions(self) -> tuple[StageDefinition, ...]:
        return tuple(self._definitions.values())
