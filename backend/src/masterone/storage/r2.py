"""R2 helper: build store from settings, key conventions."""

from __future__ import annotations

from masterone.config import Settings
from masterone.storage.object_store import R2ObjectStore


def get_store(settings: Settings) -> R2ObjectStore | None:
    if not settings.r2_enabled:
        return None
    options = ({"presign_endpoint_url": settings.r2_presign_endpoint_url}
               if settings.r2_presign_endpoint_url else {})
    return R2ObjectStore(
        settings.r2_endpoint_url,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket,
        **options,
    )


# ── Key conventions ───────────────────────────────────────────────────────────

def export_key(run_id: str, export_id: str) -> str:
    return f"exports/{run_id}/{export_id}.csv"


def source_file_key(run_id: str, file_id: str, filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_")
    return f"uploads/{run_id}/{file_id}/{safe}"
