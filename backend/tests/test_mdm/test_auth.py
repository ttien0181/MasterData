from io import BytesIO

from masterone.mdm.models import AuditEvent, ImportBatch
from sqlalchemy import select

CRM = b"source_record_id,name,email\nC-1,Acme,hello@acme.test\n"
ERP = b"source_record_id,name,email\nE-9,Acme,hello@acme.test\n"


def credentials(email: str):
    return {"email": email, "password": "a-long-test-password"}


def test_auth_session_csrf_and_ownership(client):
    first_user = client.get("/api/v1/auth/me").json()
    assert first_user["email"] == "steward@example.com"
    assert client.post("/api/v1/mdm/imports", files={"crm_file": ("crm.csv", BytesIO(CRM)),
                                                    "erp_file": ("erp.csv", BytesIO(ERP))},
                       headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/v1/mdm/imports", files={"crm_file": ("crm.csv", BytesIO(CRM)),
                                                    "erp_file": ("erp.csv", BytesIO(ERP))},
                       headers={"Origin": "http://evil.test"}).status_code == 403
    imported = client.post("/api/v1/mdm/imports", files={"crm_file": ("crm.csv", BytesIO(CRM)),
                                                        "erp_file": ("erp.csv", BytesIO(ERP))})
    assert imported.status_code == 201, imported.text
    import_id = imported.json()["import_id"]
    candidate_id = client.get(f"/api/v1/mdm/imports/{import_id}/candidates").json()[0]["id"]
    with client.session_factory() as db:
        assert db.get(ImportBatch, import_id).owner_user_id == first_user["id"]

    assert client.post("/api/v1/auth/register", json=credentials("other@example.com")).status_code == 201
    other = client.post("/api/v1/auth/login", json=credentials("other@example.com")).json()
    client.headers.update({"X-CSRF-Token": other["csrf_token"]})
    assert client.get(f"/api/v1/mdm/imports/{import_id}").status_code == 404
    assert client.get(f"/api/v1/mdm/imports/{import_id}/candidates").status_code == 404
    assert client.get(f"/api/v1/mdm/candidates/{candidate_id}/golden-record").status_code == 404
    assert client.patch(f"/api/v1/mdm/candidates/{candidate_id}/decision", json={"decision": "accepted"}).status_code == 404
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get(f"/api/v1/mdm/imports/{import_id}").status_code == 401

    first = client.post("/api/v1/auth/login", json=credentials("steward@example.com")).json()
    client.headers.update({"X-CSRF-Token": first["csrf_token"]})
    assert client.patch(f"/api/v1/mdm/candidates/{candidate_id}/decision", json={"decision": "accepted"}).status_code == 200
    with client.session_factory() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.candidate_id == candidate_id)).actor == first_user["id"]


def test_registration_and_login_limits(client):
    assert client.post("/api/v1/auth/register", json=credentials("STEWARD@example.com")).status_code == 409
    assert client.post("/api/v1/auth/register", json={"email": "bad", "password": "short"}).status_code == 422
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=credentials("missing@example.com")).status_code == 401
    assert client.post("/api/v1/auth/login", json=credentials("missing@example.com")).status_code == 429


def test_anonymous_mdm_is_rejected(client):
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/mdm/imports", files={"crm_file": ("crm.csv", BytesIO(CRM)),
                                                    "erp_file": ("erp.csv", BytesIO(ERP))}).status_code == 401
