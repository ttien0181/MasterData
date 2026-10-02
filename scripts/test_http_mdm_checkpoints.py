"""End-to-end smoke test for MDM import checkpoints on R2."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import boto3
from fastapi.testclient import TestClient
from sqlalchemy import select

from masterone.auth.models import User
from masterone.auth.security import require_mutation
from masterone.config import get_settings
from masterone.main import app
from masterone.storage.repository import StorageJobStage


def main() -> None:
    settings = get_settings()
    r2 = boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )
    source_paths = {
        "crm_file": Path("data/golden/mdm/crm.csv"),
        "erp_file": Path("data/golden/mdm/erp.csv"),
        "bill_file": Path("data/golden/mdm/bill.csv"),
    }

    with TestClient(app) as http:
        with app.state.session_factory() as session:
            user = session.scalar(select(User).order_by(User.created_at))
        if user is None:
            raise RuntimeError("No user exists in the migrated MDM database")

        app.dependency_overrides[require_mutation] = lambda: SimpleNamespace(user=user)
        handles = []
        try:
            files = {}
            for field, path in source_paths.items():
                handle = path.open("rb")
                handles.append(handle)
                files[field] = (path.name, handle, "text/csv")
            response = http.post("/api/v1/mdm/imports", files=files)
            response.raise_for_status()
            payload = response.json()
        finally:
            for handle in handles:
                handle.close()
            app.dependency_overrides.pop(require_mutation, None)

        job_id = payload["job_id"]
        with app.state.session_factory() as session:
            stages = session.scalars(
                select(StorageJobStage).where(StorageJobStage.job_id == job_id)
            ).all()
            stage_statuses = {stage.stage: stage.status for stage in stages}

    manifest_keys = [f"jobs/{job_id}/{stage}/_SUCCESS" for stage in ("normalize", "exact_match", "result")]
    manifest_presence = {}
    for key in manifest_keys:
        try:
            manifest = r2.get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read().decode("utf-8")
            manifest_presence[key] = bool(json.loads(manifest))
        except r2.exceptions.ClientError:
            manifest_presence[key] = False

    print({
        "status_code": response.status_code,
        "job_id": job_id,
        "candidate_count": payload["candidate_count"],
        "stage_statuses": stage_statuses,
        "manifest_presence": manifest_presence,
    })


if __name__ == "__main__":
    main()
