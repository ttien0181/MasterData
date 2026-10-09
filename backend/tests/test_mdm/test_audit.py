from datetime import UTC, datetime

from masterone.auth.models import User
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun


def _create_run_with_audit(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="audited-run", owner_user_id=owner_id, status="COMPLETED"))
        db.flush()
        for second, action in enumerate(("SESSION_CREATED", "RUN_STARTED", "SESSION_RENAMED"), start=1):
            db.add(ResolutionAuditEvent(
                run_id="audited-run", actor_user_id=owner_id, action=action,
                details={"event_number": second},
                created_at=datetime(2026, 10, 7, 0, 0, second, tzinfo=UTC),
            ))


def _set_role(client, role):
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized="steward@example.com").one().role = role


def test_audit_is_paginated_filterable_and_readable_by_steward_roles(client):
    _create_run_with_audit(client)
    for role in ("STEWARD", "SENIOR_STEWARD", "POLICY_ADMIN", "AUDITOR"):
        _set_role(client, role)
        response = client.get("/api/v1/mdm/runs/audited-run/audit?limit=2")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3
        assert [event["action"] for event in payload["items"]] == ["SESSION_RENAMED", "RUN_STARTED"]
        assert payload["items"][0]["details"] == {"event_number": 3}

    filtered = client.get("/api/v1/mdm/runs/audited-run/audit?action=SESSION_CREATED")
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["action"] == "SESSION_CREATED"
    assert client.get("/api/v1/mdm/runs/audited-run/audit?offset=3").json()["items"] == []


def test_viewer_cannot_read_audit_and_steward_cannot_read_foreign_audit(client):
    _create_run_with_audit(client)
    _set_role(client, "VIEWER")
    assert client.get("/api/v1/mdm/runs/audited-run/audit").status_code == 403

    with client.session_factory.begin() as db:
        other = User(email="other-audit@example.com", email_normalized="other-audit@example.com",
                     password_hash="unused", role="STEWARD")
        db.add(other)
        db.flush()
        db.add(ResolutionRun(id="foreign-audit-run", owner_user_id=other.id))
    _set_role(client, "STEWARD")
    assert client.get("/api/v1/mdm/runs/foreign-audit-run/audit").status_code == 404
