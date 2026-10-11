"""The overview socket must enforce the same origin, session, capability and ownership rules as GET /overview."""

import pytest
from masterone.auth.models import User
from masterone.auth.security import COOKIE
from masterone.mdm.models import ResolutionRun
from starlette.websockets import WebSocketDisconnect

WS_PATH = "/api/v1/mdm/runs/{run_id}/overview/ws"


def test_owner_receives_overview_snapshot(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="owned", owner_user_id=owner_id, status="RUNNING"))
    with client.websocket_connect(WS_PATH.format(run_id="owned")) as ws:
        snapshot = ws.receive_json()
    assert snapshot["run_id"] == "owned"
    assert snapshot["status"] == "RUNNING"
    assert snapshot == client.get("/api/v1/mdm/runs/owned/overview").json()


def test_socket_refuses_runs_owned_by_someone_else(client):
    with client.session_factory.begin() as db:
        other = User(email="other@example.com", email_normalized="other@example.com",
                     password_hash="unused", role="STEWARD")
        db.add(other)
        db.flush()
        db.add(ResolutionRun(id="foreign", owner_user_id=other.id))
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(WS_PATH.format(run_id="foreign")):
            pass
    assert refused.value.code == 1008


def test_socket_requires_session_cookie(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="owned", owner_user_id=owner_id))
    client.cookies.delete(COOKIE)
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(WS_PATH.format(run_id="owned")):
            pass
    assert refused.value.code == 1008


def test_socket_rejects_foreign_origin(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    with client.session_factory.begin() as db:
        db.add(ResolutionRun(id="owned", owner_user_id=owner_id))
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(WS_PATH.format(run_id="owned"), headers={"Origin": "https://evil.test"}):
            pass
    assert refused.value.code == 1008
