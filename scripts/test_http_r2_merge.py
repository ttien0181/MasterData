"""End-to-end smoke test for the authenticated R2 merge endpoint."""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import boto3
from fastapi.testclient import TestClient
from sqlalchemy import select

from masterone.auth.security import current_session, require_mutation
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.main import app
from masterone.storage.repository import StorageJobStage


def main() -> None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    token = str(uuid.uuid4())
    source_paths = [
        Path("data/golden/mdm/crm.csv"),
        Path("data/golden/mdm/erp.csv"),
        Path("data/golden/mdm/bill.csv"),
    ]
    object_keys = [f"incoming/http-e2e-{token}-{path.name}" for path in source_paths]
    for path, key in zip(source_paths, object_keys, strict=True):
        client.upload_file(str(path), settings.r2_bucket, key, ExtraArgs={"ContentType": "text/csv"})

    fake_auth = SimpleNamespace(user=SimpleNamespace(id="e2e-test-user"))
    with TestClient(app) as http:
        with app.state.session_factory() as session:
            user = session.scalar(select(User).order_by(User.created_at))
        if user is None:
            raise RuntimeError("No user exists in the migrated MDM database")
        fake_auth.user = user
        app.dependency_overrides[require_mutation] = lambda: fake_auth
        app.dependency_overrides[current_session] = lambda: fake_auth
        try:
            response = http.post(
                "/api/v1/large-imports/merge",
                json={"object_keys": object_keys, "cleanup_sources": True},
            )
            response.raise_for_status()
            payload = response.json()
            status_response = http.get(f"/api/v1/large-imports/jobs/{payload['job_id']}")
            status_response.raise_for_status()
            resume_response = http.post(
                "/api/v1/large-imports/merge",
                json={"job_id": payload["job_id"], "object_keys": object_keys, "cleanup_sources": True},
            )
            resume_response.raise_for_status()
            resume_payload = resume_response.json()
        finally:
            app.dependency_overrides.pop(require_mutation, None)
            app.dependency_overrides.pop(current_session, None)

    output_key = payload["output_object_key"]
    job_id = payload["job_id"]
    body = client.get_object(Bucket=settings.r2_bucket, Key=output_key)["Body"].read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(body)))
    manifest_key = f"jobs/{job_id}/merge/_SUCCESS"
    manifest = client.get_object(Bucket=settings.r2_bucket, Key=manifest_key)["Body"].read().decode("utf-8")

    with app.state.session_factory() as session:
        stage = session.scalar(select(StorageJobStage).where(
            StorageJobStage.job_id == job_id,
            StorageJobStage.stage == "merge",
        ))
        stage_status = stage.status if stage else None

    print({
        "status_code": response.status_code,
        "job_id": job_id,
        "output_key": output_key,
        "rows_written": payload["rows_written"],
        "header_columns": len(rows[0]),
        "data_rows": len(rows) - 1,
        "stage_status": stage_status,
        "manifest_present": bool(manifest),
        "status_endpoint": status_response.json()["status"],
        "resume_same_output": resume_payload["output_object_key"] == output_key,
    })


if __name__ == "__main__":
    main()
