"""S3-compatible object storage adapter for Cloudflare R2."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


class _BorrowedStream:
    """Let the transfer SDK close its body without closing the caller's file."""

    def __init__(self, stream: BinaryIO):
        self._stream = stream

    def __getattr__(self, name: str):
        return getattr(self._stream, name)

    def close(self) -> None:
        # The caller owns the stream lifetime, including when an upload fails.
        pass


class R2ObjectStore:
    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        )

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
        return response["Body"].read()

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
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
