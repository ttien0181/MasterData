from masterone.mdm.models import ResolutionRun


def credentials(email: str):
    return {"email": email, "password": "a-long-test-password"}


def test_auth_session_csrf_and_run_ownership(client):
    first_user = client.get("/api/v1/auth/me").json()
    with client.session_factory() as db:
        db.add(ResolutionRun(id="owned-run", owner_user_id=first_user["id"]))
        db.commit()
    assert client.post("/api/v1/mdm/runs/owned-run/start", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/v1/mdm/runs/owned-run/start", headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.post("/api/v1/auth/register", json=credentials("other@example.com")).status_code == 201
    other = client.post("/api/v1/auth/login", json=credentials("other@example.com")).json()
    client.headers.update({"X-CSRF-Token": other["csrf_token"]})
    assert client.get("/api/v1/mdm/runs/owned-run").status_code == 404
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    first = client.post("/api/v1/auth/login", json=credentials("steward@example.com")).json()
    client.headers.update({"X-CSRF-Token": first["csrf_token"]})
    assert client.get("/api/v1/mdm/runs/owned-run").status_code == 200


def test_registration_and_login_limits(client):
    assert client.post("/api/v1/auth/register", json=credentials("STEWARD@example.com")).status_code == 409
    assert client.post("/api/v1/auth/register", json={"email": "bad", "password": "short"}).status_code == 422
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=credentials("missing@example.com")).status_code == 401
    assert client.post("/api/v1/auth/login", json=credentials("missing@example.com")).status_code == 429


def test_local_next_dev_origin_can_register(client):
    url = "/api/v1/auth/register"
    for origin in ("http://localhost:3001", "http://127.0.0.1:3000"):
        preflight = client.options(url, headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin

    registered = client.post(url, json=credentials("next-port@example.com"),
                             headers={"Origin": "http://localhost:3001"})
    assert registered.status_code == 201
    assert registered.headers["access-control-allow-origin"] == "http://localhost:3001"
    blocked = client.options(url, headers={
        "Origin": "http://localhost.evil.test:3001",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert blocked.status_code == 400


def test_anonymous_mdm_is_rejected(client):
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/mdm/runs").status_code == 401
