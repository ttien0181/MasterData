import csv
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from masterone.config import Settings, get_settings
from masterone.mdm.engine_adapter import EngineResultAdapter, engine_context, select_engine
from masterone.mdm.migrate import alembic_config
from masterone.mdm.models import (
    ResolutionEngineSelection,
    ResolutionMergeEvent,
    ResolutionNegativeEdge,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
    ResolutionTask,
)
from masterone.mdm.pipeline.decision_semantics import enabled
from masterone.mdm.resolution_service import run_pipeline
from sqlalchemy import create_engine

from .test_decision_semantics import record
from .test_generation_staging import snapshot  # noqa: F401, F811
from .test_resolution_items import seed_run


def result(**updates):
    return {"id": "pair", "score": 12.5, "rule_score": 12.5, "match_probability": .97,
        "decision": "REVIEW", "triage_status": "REVIEW", "primary_group": "B", "subgroup": "B1_NORMALIZED",
        "field_evidence": [{"field": "name", "left": "A", "right": "Á", "similarity": .9,
            "state": "NX", "provenance": "NORMALIZATION", "normalization_tags": ["NAME_FOLD"],
            "debug": "x" * 1000000}], **updates}


def test_adapter_keeps_rule_units_and_projects_bounded_evidence():
    source = result()
    actual = EngineResultAdapter().pair(source)
    assert actual["score"] == 12.5 and actual["match_probability"] == .97
    assert actual["primary_group"] == "B" and actual["subgroup"] == "B1_NORMALIZED"
    assert actual["field_evidence"][0]["normalization_tags"] == ["NAME_FOLD"]
    assert "debug" not in actual["field_evidence"][0]
    assert "debug" in source["field_evidence"][0]


@pytest.mark.parametrize("changes", [{"rule_score": None}, {"score": .97}, {"match_probability": 1.2},
    {"score": float("nan"), "rule_score": float("nan")}, {"primary_group": "NEW_GROUP"}])
def test_ambiguous_or_invalid_score_fails_explicitly(changes):
    with pytest.raises((ValueError, TypeError)):
        EngineResultAdapter().pair(result(**changes))


def test_probability_cannot_fill_missing_rule_score():
    payload = result()
    payload.pop("rule_score")
    with pytest.raises(ValueError, match="RULE_SCORE_REQUIRED"):
        EngineResultAdapter().pair(payload)


@pytest.mark.parametrize("group", "ABCD")
def test_group_subgroup_round_trip(group):
    assert EngineResultAdapter().pair(result(primary_group=group, subgroup=group + "_TEST"))["subgroup"] == group + "_TEST"


@pytest.mark.parametrize("group", "ABCD")
def test_api_group_subgroup_filters_preserve_categories(client, group):
    run_id = seed_run(client)
    subgroup = group + "_TEST"
    with client.session_factory.begin() as db:
        pair = db.get(ResolutionPair, "uncertain-bc")
        pair.primary_group, pair.subgroup = group, subgroup
    prefix = f"/api/v1/mdm/runs/{run_id}/items/review_pairs"
    page = client.get(prefix, params={"primary_group": group, "subgroup": subgroup, "limit": 1}).json()
    assert page["total"] == 1
    assert page["items"][0]["primary_group"] == group
    assert page["items"][0]["subgroup"] == subgroup
    assert client.get(prefix, params={"primary_group": group, "subgroup": "absent"}).json()["total"] == 0
    detail = client.get(prefix + "/uncertain-bc").json()
    assert detail["pairs"][0]["primary_group"] == group
    assert detail["pairs"][0]["subgroup"] == subgroup


@pytest.mark.parametrize("decision", ["BLOCKED", "DEFERRED", "NON_MATCH"])
def test_adapter_does_not_invent_non_match(decision):
    output = EngineResultAdapter().pair(result(decision=decision))
    assert output["decision"] == decision
    assert "user_decision" not in output


def test_profiles_are_isolated_and_flag_default_safe():
    assert Settings(_env_file=None).mdm_engine_v2_enabled is False
    original = enabled()
    with engine_context({"decision_semantics_v2_enabled": True}):
        assert enabled()
        with engine_context({"decision_semantics_v2_enabled": False}):
            assert not enabled()
        assert enabled()
    assert enabled() == original


def test_new_engine_api_dto_auto_policy_and_pinned_retry(snapshot, monkeypatch):  # noqa: F811
    factory, _, _ = snapshot
    monkeypatch.setattr(get_settings(), "mdm_engine_v2_enabled", True)
    run_pipeline("run", factory, lambda *args: None)
    with factory() as db:
        profile = db.get(ResolutionEngineSelection, "run")
        assert profile.engine_version == "p119_person_v2"
        assert all(profile.configuration.values())
        assert db.query(ResolutionProposal).one().status == "AUTO_READY"
        pair = db.query(ResolutionPair).first()
        pair_id = pair.id
    with factory.begin() as db:
        pair = db.get(ResolutionPair, pair_id)
        pair.user_decision = "DEFERRED"
        pair.revision = 8
        proposal = db.query(ResolutionProposal).one()
        proposal.golden_fields = {**proposal.golden_fields, "_golden_override": {"name": "Saved human"}}
    monkeypatch.setattr(get_settings(), "mdm_engine_v2_enabled", False)
    run_pipeline("run", factory, lambda *args: None)
    with factory() as db:
        assert db.get(ResolutionEngineSelection, "run").engine_version == "p119_person_v2"
        assert db.get(ResolutionPair, pair_id).user_decision == "DEFERRED"
        assert db.get(ResolutionPair, pair_id).revision == 8
        assert db.query(ResolutionProposal).one().golden_fields["_golden_override"] == {"name": "Saved human"}


def test_grandfather_existing_serving_run(client, monkeypatch):
    run_id = seed_run(client)
    monkeypatch.setattr(get_settings(), "mdm_engine_v2_enabled", True)
    profile = select_engine(client.session_factory, run_id)
    assert profile["decision_semantics_v2_enabled"] is False
    with client.session_factory() as db:
        assert db.get(ResolutionEngineSelection, run_id).engine_version == "p119_current"


def test_new_engine_materializes_existing_frontend_dtos(client, monkeypatch):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.query(ResolutionReviewCase).delete()
        db.query(ResolutionPair).delete()
        db.query(ResolutionProposal).delete()
        db.add(ResolutionTask(id="adapter-task", run_id=run_id))
        for row in db.query(ResolutionRecord).filter_by(run_id=run_id):
            payload = record(row.id, email="common@example.test", dob="1990-01-01", address="Hanoi")
            row.normalized_fields = payload["normalized_fields"]
            row.raw_fields = payload["raw_fields"]
            row.quality_flags = payload["quality_flags"]
    monkeypatch.setattr(get_settings(), "mdm_engine_v2_enabled", True)
    run_pipeline(run_id, client.session_factory, lambda *args: None)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    overview = client.get(prefix + "/overview")
    assert overview.status_code == 200 and overview.json()["status"] == "COMPLETED"
    listing = client.get(prefix + "/items/merge?limit=1").json()
    assert listing["total"] == 1 and listing["items"][0]["origin"] == "AUTO"
    detail = client.get(prefix + "/items/merge/" + listing["items"][0]["id"]).json()
    assert len(detail["records"]) == 3 and len(detail["pairs"]) == 3
    assert all(pair["score"] > 1 for pair in detail["pairs"])  # rule units, not probability
    assert all(pair["primary_group"] in "ABCD" for pair in detail["pairs"])
    assert client.get(prefix + "/items/review_pairs").json()["total"] == 0
    assert client.get(prefix + "/analytics").status_code == 200


def test_existing_api_review_batch_manual_golden_audit_export(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    page = client.get(prefix + "/items/review_pairs?offset=0&limit=1").json()
    assert page["total"] == 1 and page["items"][0]["revision"] == 1
    assert client.get(prefix + "/items/review_pairs?offset=1&limit=1").json()["items"] == []
    assert client.get(prefix + "/items/review_pairs?limit=101").status_code == 422
    detail = client.get(prefix + "/items/review_pairs/uncertain-bc")
    assert detail.status_code == 200 and len(detail.content) < 65536
    assert {"records", "field_options", "choices", "missing_fields", "pairs"} <= detail.json().keys()
    decision = client.post(prefix + "/review-items/transition", json={"item_ids": ["case-bc"], "decision": "no_merge"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["changed_count"] == 1
    with client.session_factory() as db:
        assert db.get(ResolutionPair, "uncertain-bc").user_decision == "REJECTED"
        assert db.query(ResolutionNegativeEdge).count() == 0
    with client.session_factory.begin() as db:
        alternate = db.get(ResolutionRecord, "b")
        alternate.raw_fields = {**alternate.raw_fields, "name": "Alternative name"}
    golden = client.put(prefix + "/proposals/safe-ab/golden-record", json={"fields": {"name": "User chosen"}})
    assert golden.status_code == 200
    shown = client.get(prefix + "/proposals/safe-ab/golden-record").json()
    assert shown["golden_fields"]["name"]["value"] == "User chosen"
    assert shown["golden_fields"]["name"]["provenance"] == "STEWARD_OVERRIDE"
    export = client.post(prefix + "/exports", json={"acknowledge_unreviewed": True,
        "choices": {"safe-ab": {"name": "b"}}})
    assert export.status_code == 200, export.text
    rows = list(csv.DictReader(StringIO(export.content.decode("utf-8-sig"))))
    assert next(row for row in rows if row["resolution_status"] == "MERGED")["full_name"] == "User chosen"
    assert sum(int(row["record_count"]) for row in rows) == 3
    assert client.get(prefix + "/exports").status_code == 200
    assert client.get(prefix + "/audit?limit=1&offset=0").json()["total"] >= 2


def test_manual_review_new_engine_keeps_profile_when_global_flag_off(client):
    run_id = seed_run(client)
    profile = {f: True for f in ("decision_semantics_v2_enabled", "cluster_safety_v2_enabled", "generation_staging_enabled")}
    with client.session_factory.begin() as db:
        db.add(ResolutionEngineSelection(run_id=run_id, engine_version="p119_person_v2", configuration=profile))
        for row in db.query(ResolutionRecord).filter_by(run_id=run_id):
            row.normalized_fields = record(row.id, email="common@example.test")["normalized_fields"]
        proposal = db.get(ResolutionProposal, "safe-ab")
        proposal.golden_fields = {**proposal.golden_fields, "_golden_override": {"name": "Human"}}
    response = client.post(f"/api/v1/mdm/runs/{run_id}/review-pairs/uncertain-bc/decision", json={"decision": "merge", "revision": 1})
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        golden = db.get(ResolutionProposal, response.json()["proposal_id"]).golden_fields
        assert golden["survivorship_version"] == "p119_golden_v3"
        assert golden["_golden_override"] == {"name": "Human"}


def test_golden_edit_invalidates_preview(client):
    run_id = seed_run(client)
    prefix = f"/api/v1/mdm/runs/{run_id}"
    preview = client.post(prefix + "/previews", json={"acknowledge_unreviewed": True}).json()
    assert "preview_id" in preview
    assert client.put(prefix + "/proposals/safe-ab/golden-record", json={"fields": {"name": "Edited"}}).status_code == 200
    stale = client.post(prefix + f"/previews/{preview['preview_id']}/execute", json={"execute_request_id": "execute"})
    assert stale.status_code == 409
    fresh = client.post(prefix + "/previews", json={"acknowledge_unreviewed": True}).json()
    executed = client.post(prefix + f"/previews/{fresh['preview_id']}/execute", json={"execute_request_id": "fresh"})
    assert executed.status_code == 200, executed.text
    with client.session_factory() as db:
        event = db.query(ResolutionMergeEvent).filter_by(preview_id=fresh["preview_id"]).one()
        assert event.golden_fields["name"]["value"] == "Edited"
        assert event.golden_fields["name"]["provenance"] == "STEWARD_OVERRIDE"


def test_explicit_clear_restores_materialized_golden(client):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        proposal = db.get(ResolutionProposal, "safe-ab")
        golden = dict(proposal.golden_fields)
        golden["fields"] = {**golden["fields"], "name": {"value": "Old override", "provenance": "STEWARD_OVERRIDE"}}
        golden["_golden_override"] = {"name": "Old override"}
        proposal.golden_fields = golden
    path = f"/api/v1/mdm/runs/{run_id}/proposals/safe-ab/golden-record"
    assert client.put(path, json={"fields": {}}).status_code == 200
    cleared = client.get(path).json()
    assert cleared["steward_override"] == {}
    assert cleared["golden_fields"]["name"]["value"] == "An"


def test_text_plain_json_retains_schema_revision_and_csrf_validation(client):
    run_id = seed_run(client)
    path = f"/api/v1/mdm/runs/{run_id}/review-pairs/uncertain-bc/decision"
    headers = {"Content-Type": "text/plain;charset=UTF-8"}
    assert client.post(path, content='{"decision":"no_merge","revision":99}', headers=headers).status_code == 409
    assert client.post(path, content='{"decision":"unknown","revision":1}', headers=headers).status_code == 422
    assert client.post(path, content='not json', headers=headers).status_code == 422
    assert client.post(path, content='{"decision":"no_merge","revision":1}',
                       headers={**headers, "X-CSRF-Token": "invalid"}).status_code == 403


def test_detail_size_bound_is_explicit_not_silent_truncation(client):
    run_id = seed_run(client)
    with client.session_factory.begin() as db:
        db.add(ResolutionProposal(id="oversize", run_id=run_id,
            member_record_ids=[str(i) for i in range(201)], golden_fields={},
            status="REVIEW_REQUIRED", origin="REVIEW"))
    detail = client.get(f"/api/v1/mdm/runs/{run_id}/items/review/oversize")
    assert detail.status_code == 413
    assert "pair review queue" in detail.json()["detail"]


def test_actual_frontend_api_backend_smoke(client):
    """Use actual unchanged api.ts; HTTP bridge authenticates fixture requests only."""
    run_id = seed_run(client)

    class Bridge(BaseHTTPRequestHandler):
        def request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {name: self.headers[name] for name in ("Content-Type", "X-CSRF-Token") if name in self.headers}
            response = client.request(self.command, self.path, content=body, headers=headers)
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = do_POST = do_PUT = request  # noqa: N815 -- stdlib handler API

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(["node", str(root / "scripts/smoke_frontend_api.mjs")], cwd=root,
            env={**os.environ, "NEXT_PUBLIC_API_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                 "SMOKE_RUN": run_id, "SMOKE_CSRF": client.headers["X-CSRF-Token"]},
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS: actual frontend api.ts" in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_engine_selection_migration_rollback_retains_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "selection.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "head")
    from masterone.auth.models import User
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(url)
    factory = sessionmaker(engine)
    with factory.begin() as db:
        db.add(User(id="owner", email="owner@example.test", email_normalized="owner@example.test", password_hash="unused"))
        db.flush()
        db.add(ResolutionRun(id="run", owner_user_id="owner"))
        db.flush()
        db.add(ResolutionEngineSelection(run_id="run", engine_version="p119_person_v2",
            configuration={f: True for f in ("decision_semantics_v2_enabled", "cluster_safety_v2_enabled", "generation_staging_enabled")}))
    command.downgrade(config, "0022_generation_staging")
    with engine.connect() as connection:
        from sqlalchemy import inspect
        assert inspect(connection).has_table("mdm_resolution_engine_selections")
    command.upgrade(config, "head")
    with factory() as db:
        assert db.get(ResolutionEngineSelection, "run").engine_version == "p119_person_v2"
        assert all(db.get(ResolutionEngineSelection, "run").configuration.values())
    engine.dispose()
