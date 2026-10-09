"""Exercise actual auth/capability dependencies and HTTP endpoints."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from masterone.auth.manage_role import change_role
from masterone.auth.models import User
from masterone.auth.security import ROLES, capabilities_for_role, current_session, require_capability
from masterone.mdm.models import ResolutionRun, ResolutionTask


def set_role(client, role):
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized="steward@example.com").one().role = role


@pytest.mark.parametrize("role", ROLES)
def test_auth_contract_matches_enforced_permissions(client, role):
    set_role(client, role)
    me = client.get("/api/v1/auth/me").json()
    login = client.post("/api/v1/auth/login", json={
        "email": "steward@example.com", "password": "a-long-test-password",
    }).json()
    assert me["role"] == login["role"] == role
    assert me["capabilities"] == login["capabilities"] == capabilities_for_role(role)
    assert me["id"] == login["id"]
    assert me["csrf_token"] and login["csrf_token"]


def test_registration_cannot_choose_a_privileged_role(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "viewer@example.com", "password": "a-long-test-password",
        "role": "SENIOR_STEWARD", "capabilities": ["export_data"],
    })
    assert response.status_code == 201
    login = client.post("/api/v1/auth/login", json={
        "email": "viewer@example.com", "password": "a-long-test-password",
    }).json()
    assert login["role"] == "VIEWER"
    assert "create_run" not in login["capabilities"]


def test_audit_read_is_available_to_stewards_and_above_but_not_viewers():
    assert "read_audit" not in capabilities_for_role("VIEWER")
    for role in ("STEWARD", "SENIOR_STEWARD", "POLICY_ADMIN", "AUDITOR"):
        assert "read_audit" in capabilities_for_role(role)


OPERATIONS = [
    ("POST", "/runs", None),
    ("POST", "/runs/owned/start", None),
    ("POST", "/runs/owned/review-items/transition", {"item_ids": ["item"], "decision": "merge"}),
    ("POST", "/runs/owned/review-items/transition", {"item_ids": ["item"], "decision": "no_merge"}),
    ("POST", "/runs/owned/merge-items/item/resolve-conflict", None),
    ("POST", "/runs/owned/previews", {}),
    ("POST", "/runs/owned/previews/preview/execute", {"execute_request_id": "request"}),
    ("POST", "/runs/owned/exports", {}),
    ("GET", "/runs/owned/source-records.csv", None),
    ("GET", "/runs/owned/exports/export/download", None),
    ("GET", "/runs/owned/exports/export/merges/event/split-preview?member_id=member", None),
    ("POST", "/runs/owned/exports/export/merges/event/split",
     {"member_id": "member", "version": "version", "execute_request_id": "request"}),
    ("PUT", "/runs/owned/proposals/item/golden-record", {"fields": {}}),
    ("POST", "/runs/owned/items/review/item/explanation", None),
    ("POST", "/runs/owned/categories/category/explanation", None),
]


@pytest.mark.parametrize("role", ["VIEWER", "AUDITOR", "POLICY_ADMIN"])
def test_read_only_roles_cannot_write_or_download_even_on_owned_run(client, role):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="owned", owner_user_id=owner_id))
    set_role(client, role)
    assert client.get("/api/v1/mdm/runs/owned").status_code == 200
    assert client.get("/api/v1/mdm/runs/owned/overview").status_code == 200
    assert client.get("/api/v1/mdm/runs/owned/exports").status_code == 200
    for method, path, body in OPERATIONS:
        kwargs = {"json": body} if body is not None else {}
        if path == "/runs":
            kwargs = {"files": [("files", ("test.csv", b"irrelevant", "text/csv"))]}
        response = client.request(method, "/api/v1/mdm" + path, **kwargs)
        assert response.status_code == 403, (role, method, path, response.text)


def test_steward_cannot_selectively_unmerge_but_senior_can_reach_owned_resource(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="owned", owner_user_id=owner_id, status="COMPLETED"))
    url = "/api/v1/mdm/runs/owned/exports/missing/merges/event/split-preview?member_id=a"
    assert client.get(url).status_code == 403
    set_role(client, "SENIOR_STEWARD")
    response = client.get(url)
    assert response.status_code == 409
    assert response.json()["detail"] == "Bản xuất đã thay đổi; hãy mở bản mới nhất"


def test_permissions_never_bypass_owner_and_csrf_checks(client):
    with client.session_factory.begin() as db:
        other = User(email="other@example.com", email_normalized="other@example.com",
                     password_hash="unused", role="STEWARD")
        db.add(other)
        db.flush()
        db.add(ResolutionRun(id="foreign", owner_user_id=other.id))
        db.add(ResolutionTask(id="foreign-task", run_id="foreign"))
    for role in ROLES:
        set_role(client, role)
        assert client.get("/api/v1/mdm/runs/foreign").status_code == 404
        assert client.get("/api/v1/mdm/runs/foreign/overview").status_code == 404
        assert client.get("/api/v1/mdm/runs/tasks/foreign-task").status_code == 404
        assert client.get("/api/v1/mdm/runs").json()["total"] == 0
    set_role(client, "STEWARD")
    assert client.post("/api/v1/mdm/runs/foreign/start").status_code == 404
    assert client.get("/api/v1/mdm/runs/foreign/source-records.csv").status_code == 404
    for headers in ({"X-CSRF-Token": "wrong"}, {"Origin": "https://evil.test"}):
        response = client.post("/api/v1/mdm/runs/foreign/previews", json={}, headers=headers)
        assert response.status_code == 403
        assert "Capability" not in response.json()["detail"]


@pytest.mark.parametrize("role,capability", [("UNKNOWN", "read"), ("SENIOR_STEWARD", "unknown")])
def test_unknown_roles_and_capabilities_fail_closed(role, capability):
    from types import SimpleNamespace

    app = FastAPI()
    app.dependency_overrides[current_session] = lambda: SimpleNamespace(user=SimpleNamespace(role=role))

    @app.get("/protected")
    def protected(auth=Depends(require_capability(capability))):
        return {"ok": True}

    assert TestClient(app).get("/protected").status_code == 403


def test_offline_role_assignment_is_explicit_and_defaults_to_dry_run(client):
    with client.session_factory() as db:
        user = db.query(User).one()
        result = change_role(db, user.email, "SENIOR_STEWARD", "STEWARD")
        assert result["applied"] is False
        db.refresh(user)
        assert user.role == "STEWARD"
        with pytest.raises(ValueError, match="Expected"):
            change_role(db, user.email, "SENIOR_STEWARD", "VIEWER", apply=True)
        change_role(db, user.email, "SENIOR_STEWARD", "STEWARD", apply=True)
    assert "selective_unmerge" in client.get("/api/v1/auth/me").json()["capabilities"]
