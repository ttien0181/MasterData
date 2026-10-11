"""R2 helper: build store from settings, key conventions."""

from __future__ import annotations

from functools import lru_cache

from masterone.config import Settings
from masterone.storage.object_store import R2ObjectStore


def get_store(settings: Settings) -> R2ObjectStore | None:
    if not settings.r2_enabled:
        return None
    # One boto3 client per credential set: boto3 clients are thread-safe, and
    # upload/validate/commit call this once per part.
    return _cached_store(settings.r2_endpoint_url, settings.r2_access_key_id,
                         settings.r2_secret_access_key, settings.r2_bucket,
                         settings.r2_presign_endpoint_url)


@lru_cache(maxsize=4)
def _cached_store(endpoint_url: str, access_key_id: str, secret_access_key: str, bucket: str,
                  presign_endpoint_url: str) -> R2ObjectStore:
    options = {"presign_endpoint_url": presign_endpoint_url} if presign_endpoint_url else {}
    return R2ObjectStore(endpoint_url, access_key_id, secret_access_key, bucket, **options)


# ── Key conventions ───────────────────────────────────────────────────────────

def export_key(run_id: str, export_id: str) -> str:
    return f"exports/{run_id}/{export_id}.csv"


def source_file_key(run_id: str, file_id: str, filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_")
    return f"uploads/{run_id}/{file_id}/{safe}"
