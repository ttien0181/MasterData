from pathlib import PurePosixPath
from uuid import uuid4


class R2SignedUrlService:
    """Create short-lived upload URLs for Cloudflare R2 (S3-compatible)."""

    def __init__(self, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket_name: str, ttl_seconds: int):
        import boto3

        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key, region_name="auto",
        )
        self._bucket_name = bucket_name
        self._ttl = ttl_seconds

    def create_upload_url(self, filename: str, content_type: str) -> tuple[str, str]:
        safe_name = PurePosixPath(filename).name
        object_name = f"incoming/{uuid4()}-{safe_name}"
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket_name, "Key": object_name, "ContentType": content_type},
            ExpiresIn=self._ttl, HttpMethod="PUT",
        )
        return object_name, url


def create_r2_client(endpoint_url: str, access_key_id: str, secret_access_key: str):
    import boto3

    return boto3.client(
        "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key, region_name="auto",
    )
