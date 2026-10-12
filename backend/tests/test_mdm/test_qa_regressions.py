"""Failures discovered by the full-system QA rehearsal; no live credentials."""
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.storage.object_store import R2ObjectStore
from sqlalchemy import create_engine, inspect, text


@pytest.mark.parametrize("value", ["2023-02-29", "2024-02-30", "31/04/2000", "1900-02-29", "2100-02-29"])
def test_impossible_calendar_dates_are_invalid_and_preserve_input(value):
    from masterone.mdm.pipeline.standardization import normalize_dob, parse_dob
    assert normalize_dob(value) == (value, "INVALID")
    assert parse_dob(value)["quality"] == "INVALID"
    assert parse_dob(value)["canonical"] == value


@pytest.mark.parametrize("value,canonical", [("2000-02-29", "2000-02-29"), ("29/02/2024", "2024-02-29"), ("31/01/1990", "1990-01-31")])
def test_real_calendar_dates_remain_valid(value, canonical):
    from masterone.mdm.pipeline.standardization import normalize_dob, parse_dob
    assert normalize_dob(value) == (canonical, "VALID")
    assert parse_dob(value)["quality"] == "VALID"
    assert parse_dob(value)["canonical"] == canonical


class MemoryStore:
    def __init__(self, fails=False):
        self.objects = {}
        self.deleted = []
        self.fails = fails

    def upload_stream(self, stream, key, content_type):
        self.objects[key] = stream.read()
        if self.fails:
            raise OSError("synthetic storage interruption")

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


def test_configured_storage_failure_does_not_commit_an_import(client, monkeypatch):
    store = MemoryStore(fails=True)
    monkeypatch.setattr("masterone.mdm.resolution_import.get_store", lambda _: store)
    response = client.post("/api/v1/mdm/runs", files={
        "files": ("qa.csv", b"record_id,source,name\na,CRM,Synthetic QA\n", "text/csv")})
    assert response.status_code == 502
    with client.session_factory() as db:
        assert db.query(ResolutionRun).count() == db.query(ResolutionRecord).count() == 0
    assert not store.objects
    assert len(store.deleted) == 1


def test_later_file_failure_compensates_only_owned_source_objects(client, monkeypatch):
    store = MemoryStore()
    store.objects["unrelated/keep"] = b"existing object"
    monkeypatch.setattr("masterone.mdm.resolution_import.get_store", lambda _: store)
    response = client.post("/api/v1/mdm/runs", files=[
        ("files", ("good.csv", b"record_id,source,name\na,CRM,Synthetic QA\n", "text/csv")),
        ("files", ("bad.csv", b"record_id,source,name\nb,CRM,\n", "text/csv")),
    ])
    assert response.status_code == 422
    assert store.objects == {"unrelated/keep": b"existing object"}
    with client.session_factory() as db:
        assert db.query(ResolutionRun).count() == db.query(ResolutionRecord).count() == 0


@pytest.mark.parametrize("fails", [False, True])
def test_download_bytes_closes_stream_even_when_read_fails(monkeypatch, fails):
    store = R2ObjectStore("https://r2.example.test", "test-access", "test-secret", "qa")
    body = Mock()
    if fails:
        body.read.side_effect = OSError("synthetic read failure")
    else:
        body.read.return_value = b"data"
    monkeypatch.setattr(store.client, "get_object", lambda **_: {"Body": body})
    if fails:
        with pytest.raises(OSError):
            store.download_bytes("qa/object")
    else:
        assert store.download_bytes("qa/object") == b"data"
    body.close.assert_called_once()


def test_presign_endpoint_is_reachable_by_browser():
    store = R2ObjectStore("http://storage:9000", "test-access", "test-secret", "qa",
                          presign_endpoint_url="http://127.0.0.1:19000")
    assert urlparse(store.presigned_url("qa/object")).netloc == "127.0.0.1:19000"
    assert store.client.meta.endpoint_url == "http://storage:9000"


def test_production_schema_validation_never_creates_or_upgrades_tables(tmp_path):
    from masterone.mdm.migrate import validate_current
    engine = create_engine("sqlite:///" + (tmp_path / "gate.db").as_posix())
    try:
        with pytest.raises(RuntimeError, match="schema missing"):
            validate_current(engine)
        assert not inspect(engine).get_table_names()
        with engine.begin() as db:
            db.execute(text("CREATE TABLE alembic_version(version_num VARCHAR(64))"))
            db.execute(text("INSERT INTO alembic_version VALUES ('0001_mdm_baseline')"))
        with pytest.raises(RuntimeError, match="out of date"):
            validate_current(engine)
        with engine.connect() as db:
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_mdm_baseline"
    finally:
        engine.dispose()


def test_in_memory_stage_runner_preserves_explicit_source_identity(tmp_path):
    import pyarrow.parquet as pq
    runner = StageRunner("qa", "attempt", output_dir=tmp_path)
    try:
        output = runner.run_all([{"id": "a", "external_id": "real-source-id", "source_system": "ERP",
                                 "entity_type": "PERSON", "raw_fields": {"name": "Synthetic QA"}}])
        assert output["candidate_count"] == 0
        row = pq.read_table(tmp_path / "qa_prepared.parquet").to_pylist()[0]
        assert row["external_id"] == "real-source-id" and row["source_system"] == "ERP"
    finally:
        runner.con.close()


def test_stage_runner_surfaces_normalization_failure(tmp_path, monkeypatch):
    def broken(*args):
        raise ValueError("synthetic normalization failure")
    monkeypatch.setattr("masterone.mdm.pipeline.stage_runner.standardize", broken)
    runner = StageRunner("qa", "attempt", output_dir=tmp_path)
    try:
        with pytest.raises(ValueError, match="synthetic normalization failure"):
            runner.run_all([{"id": "a", "raw_fields": {"name": "Synthetic QA"}}])
        assert not any(stage.stage_name == "NORMALIZE" for stage in runner.stage_results)
    finally:
        runner.con.close()


def test_numbered_gemini_keys_are_accepted_without_extra_requests(monkeypatch):
    from masterone.config import Settings
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        monkeypatch.setenv(name, "")
    selected = Settings(_env_file=None, gemini_api_key_1="mock-1", gemini_api_key_2="mock-2",
                        gemini_api_key_3="mock-3", gemini_max_retries=0)
    assert selected.gemini_api_key == "mock-1" and selected.gemini_max_retries == 0
    explicit = Settings(_env_file=None, gemini_api_key="explicit-mock", gemini_api_key_1="mock-1")
    assert explicit.gemini_api_key == "explicit-mock"


def test_stage_runner_keeps_null_groups_valid_at_serving_boundary(tmp_path):
    from masterone.mdm.engine_adapter import EngineResultAdapter
    records = [{"id": key, "source_system": "CRM", "entity_type": "PERSON",
                "raw_fields": {"name": "Nguyen Van An", "phone": "0901234567",
                               "government_id": identity, "government_id_type": "CCCD"}}
               for key, identity in (("a", "001000000001"), ("b", "001000000002"), ("c", "001000000001"))]
    runner = StageRunner("qa-nulls", "attempt", output_dir=tmp_path)
    try:
        output = runner.run_all(records)
        assert output["pairs"]
        assert any(pair["primary_group"] is None for pair in output["pairs"])
        for pair in output["pairs"]:
            EngineResultAdapter().pair(pair)
        assert all(pair["decision"] != "MERGEABLE" for pair in output["pairs"]
                   if {pair["left_id"], pair["right_id"]} & {"b"})
    finally:
        runner.con.close()


def test_explicit_migration_target_wins_over_environment(tmp_path, monkeypatch):
    from masterone.mdm.migrate import upgrade_database, validate_current
    intended = "sqlite:///" + (tmp_path / "intended.db").as_posix()
    unrelated = tmp_path / "untouched.db"
    monkeypatch.setenv("MDM_DATABASE_URL", "sqlite:///" + unrelated.as_posix())
    upgrade_database(intended)
    engine = create_engine(intended)
    try:
        validate_current(engine)
        assert not unrelated.exists()
    finally:
        engine.dispose()


def test_default_stage_workspaces_never_reuse_another_attempt():
    # The full-baseline plugin supplies an explicit shared test directory.
    # Exercise the production constructor's actual defaults instead.
    from inspect import unwrap
    initialize = unwrap(StageRunner.__init__)
    first, second = StageRunner.__new__(StageRunner), StageRunner.__new__(StageRunner)
    initialize(first, "same-run", "attempt-1")
    initialize(second, "same-run", "attempt-2")
    try:
        assert first.output_dir != second.output_dir
        assert first.run_all([])["candidate_count"] == 0
        assert second.run_all([{"id": "a", "raw_fields": {"name": "Nguyen Van An"}}])["candidate_count"] == 0
    finally:
        first.con.close()
        second.con.close()


def test_indexed_union_guards_match_full_pair_scan(monkeypatch, tmp_path):
    from masterone.mdm.pipeline import clustering
    from masterone.mdm.pipeline.cluster_guards import run_cluster_guards
    original = clustering.build_proposals
    state = {"checked": 0}
    def build(records, pairs, *args, **kwargs):
        state["pairs"] = pairs
        return original(records, pairs, *args, **kwargs)
    def check(left, right, by_id, **kwargs):
        indexed = run_cluster_guards(left, right, by_id, **kwargs)
        full = run_cluster_guards(left, right, by_id, **{**kwargs, "pairs": state["pairs"]})
        assert indexed == full
        state["checked"] += 1
        return indexed
    monkeypatch.setattr("masterone.mdm.pipeline.stage_runner.build_proposals", build)
    monkeypatch.setattr(clustering, "run_cluster_guards", check)
    records = [{"id": key, "entity_type": "PERSON", "source_system": source,
                "raw_fields": {"name": "Tran Thi Lan" if key == "c" else "Nguyen Van An",
                               "phone": "0987654321" if key == "c" else "0901234567",
                               "email": "qc@example.com" if key == "c" else "qa@example.com",
                               "dob": "1990-01-01", "government_id": "001000000002" if key == "c" else "001000000001",
                               "government_id_type": "CCCD", "address": "12 pho Hue Ha Noi"}}
               for key, source in (("a", "CRM"), ("b", "ERP"), ("c", "BILL"))]
    runner = StageRunner("guards", "attempt", output_dir=tmp_path)
    try:
        runner.run_all(records)
        assert state["checked"] > 0
    finally:
        runner.con.close()


def test_duplicates_across_batches_1_5_8_and_sources_reach_scoring(tmp_path):
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq
    from masterone.mdm.pipeline.standardization import standardize
    records = []
    duplicate_ids = {0: "A", 16: "B", 28: "C"}
    for i in range(32):
        rid = duplicate_ids.get(i, f"other-{i}")
        raw = {"name": "Nguyen Van An", "phone": "0901234567" if i in duplicate_ids else f"098{i:07d}"}
        normalized, quality = standardize(raw)
        records.append({"id": rid, "source_system": ("CRM", "ERP", "BILL")[i % 3],
                        "raw_fields": raw, "normalized_fields": normalized, "quality_flags": quality})
    path = tmp_path / "input.parquet"
    pq.write_table(pa.Table.from_pylist([{**record, "normalized_fields": json.dumps(record['normalized_fields'])} for record in records]), path)
    runner = StageRunner("cross-batch", "attempt", output_dir=tmp_path)
    try:
        candidates = runner.build_candidates(str(path), str(tmp_path / "candidates.parquet"), batch_size=4)
        expected = {("A", "B"), ("A", "C"), ("B", "C")}
        assert expected <= set(candidates.pairs)
        for pair in expected:
            assert "exact_phone" in candidates.pairs[pair]
        metrics = runner.score_candidates(str(path), str(tmp_path / "candidates.parquet"), str(tmp_path / "scored.parquet"))
        assert metrics["scored_pairs"] == len(candidates.pairs)
        scored = {(row["left_id"], row["right_id"]) for row in pq.read_table(tmp_path / "scored.parquet").to_pylist()}
        assert expected <= scored
    finally:
        runner.con.close()


def test_log_filter_masks_values_in_lazy_arguments():
    import logging

    from masterone.logging_config import _JSONFormatter, _PIIFilter
    record = logging.LogRecord("qa", logging.INFO, __file__, 1, "phone=%s email=%s", ("0901234567", "synthetic@example.com"), None)
    _PIIFilter().filter(record)
    output = _JSONFormatter().format(record)
    assert "0901234567" not in output and "synthetic@example.com" not in output
    assert "***567" in output


def test_json_log_does_not_serialize_exception_params_or_secrets():
    import logging
    import sys

    from masterone.logging_config import _JSONFormatter
    try:
        raise ValueError("synthetic-secret-should-not-be-logged SQL params=0901234567")
    except ValueError:
        record = logging.LogRecord("qa", logging.ERROR, __file__, 1, "safe failure", (), sys.exc_info())
    output = _JSONFormatter().format(record)
    assert "synthetic-secret" not in output and "0901234567" not in output
    assert "ValueError" in output and "frames" in output


@pytest.mark.parametrize("failed_storage,expected", [(False, 409), (True, 503)])
def test_missing_export_artifact_does_not_return_a_successful_download(client, monkeypatch, failed_storage, expected):
    from masterone.auth.models import User
    from masterone.mdm.models import ResolutionExport
    uploaded = client.post("/api/v1/mdm/runs", files={"files": (
        "qa.csv", b"record_id,source,name\na,CRM,Synthetic QA\n", "text/csv")}).json()
    run_id = uploaded["run_id"]
    with client.session_factory.begin() as db:
        user = db.query(User).filter_by(email_normalized="steward@example.com").one()
        db.add(ResolutionExport(id="missing-export", run_id=run_id, actor_user_id=user.id,
                               r2_key="qa-only/missing.csv", row_count=1, merged_group_count=0, pending_review_count=0))
    store = Mock()
    if failed_storage:
        store.exists.side_effect = OSError("synthetic unavailable storage")
    else:
        store.exists.return_value = False
    monkeypatch.setattr("masterone.api.mdm_resolution.get_store", lambda _: store)
    response = client.get(f"/api/v1/mdm/runs/{run_id}/exports/missing-export/download")
    assert response.status_code == expected
    store.presigned_url.assert_not_called()
