"""Checkpoint helpers used by existing MDM stages.

The functions only serialize stage input/output. They do not transform records
or influence matching decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from masterone.config import Settings
from masterone.storage.manifest import StageManifest, fingerprint_file
from masterone.storage.object_store import R2ObjectStore
from masterone.storage.paths import JobPaths
from masterone.storage.parquet_io import write_parquet_atomic


def save_stage_checkpoint(
    settings: Settings,
    *,
    job_id: str,
    stage: str,
    rows: list[dict[str, Any]],
    schema: dict[str, str],
    input_uri: str,
    input_fingerprint: str,
    output_key: str,
    config_hash: str,
) -> StageManifest:
    """Write a local atomic Parquet checkpoint, then publish it and its manifest."""
    paths = JobPaths(job_id)
    local_path = paths.local_stage(settings.work_dir, stage) / Path(output_key).name
    write_parquet_atomic(rows, local_path)
    output_uri = f"file://{local_path.resolve()}"
    from masterone.storage.demo_scope import local_only
    if not local_only() and all((settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket)):
        store = R2ObjectStore(settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket)
        store.upload_file(local_path, output_key, "application/vnd.apache.parquet")
        head = store.client.head_object(Bucket=settings.r2_bucket, Key=output_key)
        output_uri = f"r2://{settings.r2_bucket}/{output_key}"
        byte_count = int(head.get("ContentLength", local_path.stat().st_size))
    else:
        byte_count = local_path.stat().st_size

    manifest = StageManifest(
        job_id=job_id,
        stage=stage,
        input_uri=input_uri,
        output_uri=output_uri,
        schema=schema,
        input_fingerprint=input_fingerprint,
        config_hash=config_hash,
        code_version=settings.storage_code_version,
        row_count=len(rows),
        bytes=byte_count,
        files=(output_key,),
    )
    if output_uri.startswith("r2://"):
        store.write_manifest(paths.success(stage), manifest.to_dict())
        local_path.unlink(missing_ok=True)
    return manifest
