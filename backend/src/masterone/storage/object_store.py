"""Object storage abstractions and adapters.

ObjectStore (ABC) — interface all adapters must implement.
R2ObjectStore   — Cloudflare R2 via boto3 S3-compatible API.
LocalObjectStore — local filesystem, used in dev/test when R2 is not configured.
"""

from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO


class ObjectStore(ABC):
    """Abstract interface for object storage backends."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def upload_file(self, path: str | Path, key: str,
                    content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def upload_stream(self, stream: BinaryIO, key: str,
                      content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def download_file(self, key: str, path: str | Path) -> None: ...

    @abstractmethod
    def upload_bytes(self, data: bytes, key: str,
                     content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def download_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def presigned_url(self, key: str, expires_in: int = 3600) -> str: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def write_manifest(self, key: str, payload: dict) -> None:
        """Serialize payload as JSON and upload. Concrete classes may override."""
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self.upload_bytes(data, key, content_type="application/json")

    def upload_partition_file(self, path: str | Path, key: str) -> None:
        """Partition uploader controls file concurrency separately."""
        self.upload_file(path, key, "application/vnd.apache.parquet")

    def upload_export_file(self, path: str | Path, key: str, content_type: str,
                           progress=None) -> None:
        self.upload_file(path, key, content_type)
        if progress:
            progress(0)

    def iter_chunks(self, key: str, chunk_size: int = 1024 * 1024):
        """Bounded read-back for partition verification; no full object in RAM."""
        raise NotImplementedError("Store requires streaming partition verification")


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store — for development and tests without R2 credentials."""

    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if not p.is_relative_to(self._root.resolve()):
            raise ValueError(f"Key escapes store root: {key!r}")
        # Attempt-scoped content-addressed keys plus test/deployment workspaces
        # can exceed Win32 MAX_PATH; retain the exact object key on Windows.
        if os.name == "nt" and len(str(p)) >= 240:
            value = str(p)
            if not value.startswith("\\\\?\\"):
                p = Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
        return p

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def upload_file(self, path: str | Path, key: str,
                    content_type: str = "application/octet-stream") -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(path), str(dest))

    def upload_stream(self, stream: BinaryIO, key: str,
                      content_type: str = "application/octet-stream") -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(stream.read())

    def download_file(self, key: str, path: str | Path) -> None:
        shutil.copy2(str(self._path(key)), str(path))

    def upload_bytes(self, data: bytes, key: str,
                     content_type: str = "application/octet-stream") -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def download_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        # Local store: return a file:// URI — only meaningful for same-machine callers
        return self._path(key).as_uri()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def iter_chunks(self, key: str, chunk_size: int = 1024 * 1024):
        with self._path(key).open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk


class _BorrowedStream:
    """Let the transfer SDK close its body without closing the caller's file."""

    def __init__(self, stream: BinaryIO):
        self._stream = stream

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def close(self) -> None:
        # The caller owns the stream lifetime, including when an upload fails.
        pass


class R2ObjectStore(ObjectStore):
    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket: str,
                 *, presign_endpoint_url: str | None = None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={"total_max_attempts": 3, "mode": "standard"}),
        )
        self.presign_client = (boto3.client("s3", endpoint_url=presign_endpoint_url,
            aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            region_name="auto", config=Config(signature_version="s3v4"))
            if presign_endpoint_url else self.client)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def upload_file(self, path: str | Path, key: str, content_type: str = "application/octet-stream") -> None:
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def upload_export_file(self, path: str | Path, key: str, content_type: str,
                           progress=None) -> None:
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type},
            Callback=progress, Config=TransferConfig(use_threads=False, max_concurrency=1,
                                                    multipart_chunksize=8 * 1024**2))

    def upload_partition_file(self, path: str | Path, key: str) -> None:
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(str(path), self.bucket, key,
            ExtraArgs={"ContentType": "application/vnd.apache.parquet"},
            Config=TransferConfig(use_threads=False, max_concurrency=1))

    def iter_chunks(self, key: str, chunk_size: int = 1024 * 1024):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        try:
            while chunk := body.read(chunk_size):
                yield chunk
        finally:
            body.close()

    def upload_stream(self, stream: BinaryIO, key: str, content_type: str = "application/octet-stream") -> None:
        """Upload from the current position, leaving the caller's stream open."""
        self.client.upload_fileobj(_BorrowedStream(stream), self.bucket, key,
                                   ExtraArgs={"ContentType": content_type})

    def download_file(self, key: str, path: str | Path) -> None:
        self.client.download_file(self.bucket, key, str(path))

    def upload_bytes(self, data: bytes, key: str, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def download_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def write_manifest(self, key: str, payload: dict) -> None:
        import json

        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
            ContentType="application/json",
        )
