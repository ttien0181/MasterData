"""Characterize public boundaries before migration; no algorithm changes."""

from types import SimpleNamespace

import pytest
from masterone.api.mdm_resolution import (
    ExecutePreviewRequest,
    ExecuteSplitRequest,
    ExportRequest,
    PairDecisionRequest,
    ReviewTransition,
)
from masterone.db.base import now_utc
from masterone.main import app
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionPair,
    ResolutionPreview,
    ResolutionProposal,
    ResolutionRecord,
)
from masterone.mdm.schemas import ResolutionCountsRead, task_read
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/mdm/runs/{run_id}/start"),
    ("GET", "/api/v1/mdm/runs/{run_id}/overview"),
    ("POST", "/api/v1/mdm/runs/{run_id}/review-pairs/{pair_id}/decision"),
    ("POST", "/api/v1/mdm/runs/{run_id}/review-items/transition"),
    ("POST", "/api/v1/mdm/runs/{run_id}/export-readiness"),
    ("POST", "/api/v1/mdm/runs/{run_id}/previews/{preview_id}/execute"),
    ("GET", "/api/v1/mdm/runs/{run_id}/audit"),
    ("PUT", "/api/v1/mdm/runs/{run_id}/proposals/{proposal_id}/golden-record"),
    ("POST", "/api/v1/mdm/runs/{run_id}/exports/{export_id}/merges/{event_id}/split"),
])
def test_frontend_boundary_routes_remain_registered(method, path):
    assert method.lower() in app.openapi()["paths"][path]


def test_active_request_dtos_are_not_confused_with_legacy_schemas():
    assert PairDecisionRequest(decision="merge", revision=1).model_dump() == {
        "decision": "merge", "revision": 1}
    assert ReviewTransition(item_ids=["pair"], decision="no_merge").model_dump() == {
        "item_ids": ["pair"], "decision": "no_merge"}
    assert ExportRequest().model_dump() == {"acknowledge_unreviewed": False, "choices": {}}
    assert ExecutePreviewRequest(execute_request_id="request").model_dump() == {
        "execute_request_id": "request"}
    assert set(ExecuteSplitRequest.model_fields) == {"member_id", "version", "execute_request_id"}
    with pytest.raises(ValidationError):
        PairDecisionRequest(decision="ACCEPTED", revision=1)
    with pytest.raises(ValidationError):
        PairDecisionRequest(decision="merge", revision=0)


def test_task_and_counter_units_keep_public_keys():
    task = SimpleNamespace(id="task", run_id="run", status="QUEUED", phase="queued",
                           processed_count=0, total_count=2, error_code=None, updated_at=now_utc())
    assert set(task_read(task).model_dump()) == {
        "task_id", "run_id", "status", "phase", "processed_count", "total_count",
        "error_code", "updated_at"}
    assert set(ResolutionCountsRead.model_fields) == {
        "auto", "rejected", "review", "review_cases", "review_pairs", "manual",
        "hard_blocked", "insufficient", "group_a", "group_c", "group_d", "separate"}


@pytest.mark.parametrize("model,columns", [
    (ResolutionPair, {"user_decision", "revision", "policy_version", "field_evidence"}),
    (ResolutionProposal, {"golden_fields", "revision", "member_record_ids", "origin"}),
    (ResolutionAuditEvent, {"actor_user_id", "action", "details", "pair_id", "proposal_id"}),
    (ResolutionPreview, {"choices", "pair_set_hash", "proposal_revisions", "execute_request_id"}),
])
def test_review_golden_audit_and_preview_storage_boundaries(model, columns):
    assert columns <= set(model.__table__.columns.keys())


def test_current_record_identity_constraint_requires_explicit_migration_to_change():
    uniques = {tuple(column.name for column in item.columns)
               for item in ResolutionRecord.__table__.constraints
               if isinstance(item, UniqueConstraint)}
    # Explicit 0021 migration preserves snapshot IDs and scopes source identity.
    assert ("run_id", "source_system", "external_id") in uniques


def test_baseline_runner_does_not_inherit_live_services(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "run_merge_baseline.py"
    spec = importlib.util.spec_from_file_location("merge_baseline_runner", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("MDM_DATABASE_URL", "postgresql://must-not-be-used")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://must-not-be-used")
    env = module.isolated_environment(tmp_path)
    assert env["MDM_DATABASE_URL"].startswith("sqlite:///")
    assert env["MDM_DATABASE_URL_LOCAL"] == env["MDM_DATABASE_URL"]
    assert env["APP_ENV"] == "test"
    assert env["GEMINI_API_KEY"] == env["R2_ENDPOINT_URL"] == ""
    assert env["LLM_ENABLED"] == env["SEMANTIC_ADDRESS_ENABLED"] == "false"


def test_opt_in_baseline_plugin_isolates_default_and_respects_explicit_directory(tmp_path, monkeypatch):
    from masterone.mdm.pipeline.stage_runner import StageRunner

    from tests.merge_baseline_plugin import isolated_stage_runner_scratch

    isolated_stage_runner_scratch.__wrapped__(tmp_path, monkeypatch)
    first = StageRunner("baseline-default", "attempt")
    second = StageRunner("baseline-explicit", "attempt", output_dir=tmp_path / "explicit")
    try:
        assert first.output_dir == tmp_path / "stages"
        assert second.output_dir == tmp_path / "explicit"
    finally:
        first.con.close()
        second.con.close()


def test_stage_runner_smoke_with_current_database_shaped_records(tmp_path):
    from masterone.mdm.pipeline.stage_runner import StageRunner
    from masterone.mdm.pipeline.standardization import standardize

    raw = {"name": "Nguyễn Văn An", "phone": "0912345678", "email": "", "address": "Hà Nội"}
    normalized, quality = standardize(raw, "PERSON")
    records = [{"id": rid, "external_id": "source-" + rid, "source_system": source,
                "entity_type": "PERSON", "raw_fields": dict(raw),
                "normalized_fields": dict(normalized), "quality_flags": list(quality)}
               for rid, source in (("a", "CRM"), ("b", "ERP"))]
    runner = StageRunner("baseline-complete", "attempt", output_dir=tmp_path / "complete")
    try:
        result = runner.run_all(records, return_data=True)
        assert result["candidate_count"] == 1
        assert len(result["pairs"]) == 1
        assert (result["pairs"][0]["left_id"], result["pairs"][0]["right_id"]) == ("a", "b")
        assert [stage.stage_name for stage in runner.stage_results] == [
            "PREPARE", "NORMALIZE", "BUILD_CANDIDATES", "SCORE", "DECIDE", "CLUSTER", "MATERIALIZE"]
    finally:
        runner.con.close()
