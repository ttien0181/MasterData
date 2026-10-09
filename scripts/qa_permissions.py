"""Real local role/ownership checks with a dedicated synthetic Viewer fixture."""
import json
from pathlib import Path

from qa_live_system import Probe, synthetic
from sqlalchemy import text


def main():
    output = Path("artifacts/full-system-20261009/permissions")
    probe = Probe(output)
    credentials = {"email": "qa-viewer@example.com", "password": "local-qa-synthetic-password"}
    assert probe.client.post("/api/v1/auth/register", json=credentials).status_code in {201, 409}
    with probe.engine.begin() as db:
        db.execute(text("UPDATE users SET role='STEWARD' WHERE email_normalized=:email"), credentials)
    try:
        auth = probe.request("POST", "/api/v1/auth/login", json=credentials).json()
        probe.client.headers["X-CSRF-Token"] = auth["csrf_token"]
        created = probe.check("own_fixture_pipeline", lambda: probe.run(probe.upload(content=synthetic(100)), 100))
        assert created["status"] == "PASS"
        run_id = created["run_id"]
    finally:
        with probe.engine.begin() as db:
            db.execute(text("UPDATE users SET role='VIEWER' WHERE email_normalized=:email"), credentials)
    auth = probe.request("POST", "/api/v1/auth/login", json=credentials).json()
    probe.client.headers["X-CSRF-Token"] = auth["csrf_token"]
    assert auth["role"] == "VIEWER" and auth["capabilities"] == ["read"]

    def permissions():
        prefix = f"/api/v1/mdm/runs/{run_id}"
        probe.request("GET", prefix + "/overview")
        probe.request("GET", prefix + "/items/merge")
        probe.request("POST", "/api/v1/mdm/runs", expected=403,
                      files={"files": ("viewer.csv", synthetic(2), "text/csv")})
        probe.request("POST", prefix + "/start", expected=403)
        probe.request("POST", prefix + "/previews", expected=403,
                      json={"acknowledge_unreviewed": True})
        probe.request("GET", prefix + "/audit", expected=403)
        with probe.engine.connect() as db:
            other = db.execute(text("SELECT r.id FROM mdm_resolution_runs r JOIN users u ON u.id=r.owner_user_id "
                                    "WHERE u.email_normalized=:email ORDER BY r.created_at DESC LIMIT 1"),
                               {"email": "qa-steward@example.com"}).scalar_one()
        probe.request("GET", f"/api/v1/mdm/runs/{other}/overview", expected=404)
        return {"run_id": run_id, "read_own": True, "mutations_denied": 3,
                "audit_denied": True, "other_owner_hidden": True}

    probe.check("viewer_capabilities_and_ownership", permissions)
    (output / "browser-fixture.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    probe.save()
    probe.client.close()
    probe.engine.dispose()
    return int(any(case["status"] != "PASS" for case in probe.results))


if __name__ == "__main__":
    raise SystemExit(main())
