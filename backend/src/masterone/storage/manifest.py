"""Atomic stage manifests and fingerprints."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def fingerprint_bytes(chunks: list[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class StageManifest:
    job_id: str
    stage: str
    input_uri: str
    output_uri: str
    schema: dict[str, str]
    input_fingerprint: str
    config_hash: str
    code_version: str
    row_count: int
    bytes: int
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as temporary:
        temporary.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(target)


def read_manifest(path: str | Path) -> StageManifest:
    return StageManifest(**json.loads(Path(path).read_text(encoding="utf-8")))
