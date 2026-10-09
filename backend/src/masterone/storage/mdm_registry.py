"""Stage contracts for the MDM pipeline that currently exists in the codebase."""

from __future__ import annotations

from typing import Any

from masterone.storage.paths import JobPaths
from masterone.storage.stage_registry import StageDefinition, StageRegistry


COMMON_FIELDS = {
    "source_record_id": "string",
    "name": "string",
    "phone": "string",
    "email": "string",
    "address": "string",
    "tax_id": "string",
}


def create_mdm_stage_registry(
    job_id: str,
    input_fingerprint: str,
    config: dict[str, Any],
    code_version: str,
) -> StageRegistry:
    """Declare existing stages without embedding any matching algorithm."""
    paths = JobPaths(job_id)
    registry = StageRegistry()
    config_hash = registry.config_hash(config)

    registry.register(StageDefinition(
        name="upload",
        input_uri="client://multipart",
        output_uri=f"r2://{paths.prefix}/raw/",
        schema={"source": "string", "object_key": "string"},
        fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=code_version,
    ))
    registry.register(StageDefinition(
        name="merge",
        input_uri=f"r2://{paths.prefix}/raw/",
        output_uri=f"r2://{paths.merged()}",
        schema={"columns": "dynamic-csv-header"},
        fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=code_version,
    ))
    registry.register(StageDefinition(
        name="normalize",
        input_uri=f"r2://{paths.merged()}",
        output_uri=f"r2://{paths.normalized()}",
        schema={**COMMON_FIELDS, "normalized_fields": "json"},
        fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=code_version,
    ))
    registry.register(StageDefinition(
        name="exact_match",
        input_uri=f"r2://{paths.normalized()}",
        output_uri=f"r2://{paths.matching()}",
        schema={
            "left_record_id": "string",
            "right_record_id": "string",
            "engine_id": "string",
            "evidence": "json",
        },
        fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=code_version,
    ))
    registry.register(StageDefinition(
        name="result",
        input_uri=f"r2://{paths.matching()}",
        output_uri=f"r2://{paths.result()}",
        schema={"record_id": "string", "source": "string", "candidate_id": "string"},
        fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=code_version,
    ))
    return registry
