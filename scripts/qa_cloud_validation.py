"""Bounded synthetic QA Neon/R2 validation. No LLM generation or secret output."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.qa")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    from run_merge_baseline import isolated_environment
    os.environ.update(isolated_environment(args.output / "state"))
    for key in ("MDM_DATABASE_URL", "MDM_DATABASE_PROFILE", "AUTH_CSRF_SECRET",
                "R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        if values.get(key):
            os.environ[key] = values[key]
    result = {"scope": "dedicated QA Neon/R2; synthetic 100 rows; no LLM requests", "cases": []}
    args.output.mkdir(parents=True, exist_ok=True)

    def check(name, fn):
        start = time.monotonic()
        try:
            detail = fn() or {}
            item = {"name": name, "status": "PASS", **detail}
        except Exception as error:
            item = {"name": name, "status": "FAIL", "error_type": type(error).__name__}
        item["seconds"] = time.monotonic() - start
        result["cases"].append(item)
        (args.output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(item), flush=True)
        return item

    from masterone.config import get_settings
    from masterone.db.session import create_database
    from masterone.mdm.migrate import upgrade_database, validate_current
    from masterone.storage.r2 import get_store
    from sqlalchemy import inspect, text
    settings = get_settings()
    # Explicit QA file/profile only; never fall back to developer DB or bucket.
    if settings.mdm_database_profile != "neon" or not values.get("MDM_DATABASE_URL"):
        raise RuntimeError("QA Neon profile is required")
    if not re.fullmatch(r"https://[a-fA-F0-9]{32}\.r2\.cloudflarestorage\.com/?", settings.r2_endpoint_url):
        check("real_r2_endpoint", lambda: (_ for _ in ()).throw(ValueError()))
        return 1
    engine, _ = create_database(settings.active_mdm_database_url)
    store = get_store(settings)

    def r2_probe():
        key = "masterone-qa/20261009/" + str(uuid4()) + "/probe.txt"
        content = b"MasterOne synthetic QA cloud probe."
        try:
            store.upload_bytes(content, key, "text/plain")
            assert store.exists(key) and store.download_bytes(key) == content
            with urlopen(store.presigned_url(key), timeout=10) as response:
                assert response.read() == content
            return {"bytes": len(content), "upload_download_checksum": True, "presigned_get": True}
        finally:
            store.delete(key)

    cloud = check("real_r2_upload_download_presign_cleanup", r2_probe)

    def migrate():
        tables = inspect(engine).get_table_names()
        # First cloud bootstrap may only target an empty, user-provided QA DB.
        if "alembic_version" not in tables and tables:
            raise RuntimeError("Refusing bootstrap of an unidentified nonempty database")
        upgrade_database(settings.active_mdm_database_url)
        validate_current(engine)
        return {"schema_head": "0025", "table_count": len(inspect(engine).get_table_names())}

    migration = check("neon_qa_explicit_migration", migrate)
    if migration["status"] != "PASS" or cloud["status"] != "PASS":
        engine.dispose()
        return 1

    from fastapi.testclient import TestClient
    from masterone.main import app
    from masterone.mdm.resolution_service import run_pipeline
    from qa_live_system import synthetic
    with TestClient(app) as client:
        client.headers["Origin"] = "http://localhost:3000"
        def pipeline():
            email = f"qa-{uuid4()}@example.com"
            credentials = {"email": email, "password": "synthetic-cloud-qa-only-password"}
            assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
            with engine.begin() as db:
                db.execute(text("UPDATE users SET role='SENIOR_STEWARD' WHERE email_normalized=:email"), {"email": email})
            response = client.post("/api/v1/auth/login", json=credentials)
            assert response.status_code == 200
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            response = client.post("/api/v1/mdm/runs", files={"files": ("qa.csv", synthetic(100), "text/csv")})
            assert response.status_code == 201
            run_id = response.json()["run_id"]
            assert client.post(f"/api/v1/mdm/runs/{run_id}/start").status_code == 202
            run_pipeline(run_id, app.state.session_factory, lambda *args: None)
            with engine.connect() as db:
                count = db.scalar(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run"), {"run": run_id})
                status = db.scalar(text("SELECT status FROM mdm_resolution_runs WHERE id=:run"), {"run": run_id})
            assert count == 100 and status == "COMPLETED"
            response = client.post(f"/api/v1/mdm/runs/{run_id}/previews", json={"acknowledge_unreviewed": True})
            assert response.status_code == 200
            preview = response.json()
            response = client.post(f"/api/v1/mdm/runs/{run_id}/previews/{preview.get('preview_id',preview.get('id'))}/execute",
                                   json={"execute_request_id": str(uuid4())})
            assert response.status_code == 200
            exports = client.get(f"/api/v1/mdm/runs/{run_id}/exports").json()["items"]
            response2 = client.get(f"/api/v1/mdm/runs/{run_id}/exports/{exports[0]['id']}/download", follow_redirects=False)
            assert response2.status_code == 302
            with urlopen(response2.headers["location"], timeout=10) as downloaded:
                assert hashlib.sha256(downloaded.read()).digest() == hashlib.sha256(response.content).digest()
            assert client.get("/ready").status_code == 200
            return {"run_id": run_id, "records": count, "pipeline": "COMPLETED", "export_r2_checksum": True,
                    "execution": "real API/DB/R2; pipeline invoked in process, not cloud independent worker"}
        check("neon_r2_api_pipeline_export_100", pipeline)
    engine.dispose()
    return int(any(item["status"] != "PASS" for item in result["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
