"""Production lifespan/auth checks over in-process HTTPS against local QA PG/S3."""
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from qa_live_system import DB, ROOT
from run_merge_baseline import isolated_environment

output = Path("artifacts/full-system-20261009/production-mode")
os.environ.update(isolated_environment(output / "state"))
values = dotenv_values(ROOT / ".env.qa.local")
os.environ.update(APP_ENV="production", MDM_DATABASE_URL=DB, MDM_DATABASE_URL_LOCAL=DB,
                  AUTH_CSRF_SECRET=values["AUTH_CSRF_SECRET"], CORS_ORIGINS="http://127.0.0.1:13000",
                  R2_ENDPOINT_URL="http://127.0.0.1:19000", R2_ACCESS_KEY_ID="qa-local-access",
                  R2_SECRET_ACCESS_KEY="qa-local-secret-only", R2_BUCKET="masterone-qa")
sys.path.insert(0, str(ROOT / "backend/src"))
from fastapi.testclient import TestClient
from masterone.main import app

results = []
try:
    with TestClient(app, base_url="https://qa.local.invalid", headers={"Origin": "http://127.0.0.1:13000"}) as client:
        assert client.get("/ready").status_code == 200
        login = client.post("/api/v1/auth/login", json={"email": "qa-viewer@example.com", "password": "local-qa-synthetic-password"})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]}).status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
    results.append({"name": "production_lifespan_schema_ready_secure_cookie_session_logout", "status": "PASS",
                    "scope": "host ASGI HTTPS TestClient, actual local QA PG/S3; not a deployed TLS proxy",
                    "production_schema_validation": True, "secure_httponly_samesite": True})
except Exception as error:
    results.append({"name": "production_mode", "status": "FAIL", "error_type": type(error).__name__})
output.mkdir(parents=True, exist_ok=True)
(output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results))
raise SystemExit(int(any(case["status"] != "PASS" for case in results)))
