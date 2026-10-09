"""Lossless source mapping, namespace identity, API and migration contracts."""

import csv
import json
from io import BytesIO, StringIO

import pytest
from fastapi import HTTPException, UploadFile
from masterone.mdm.input_schema import load_contract, resolve_source_schema
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from masterone.mdm.resolution_import import parse_upload


def payload(header, rows):
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


@pytest.mark.parametrize("header,code", [
    (["id", "source", "name", "name"], "DUPLICATE_HEADER"),
    (["id", "source", "name", "ｎａｍｅ"], "UNICODE_HEADER_COLLISION"),
    (["id", "source", "name", " Name "], "UNICODE_HEADER_COLLISION"),
    (["id", "source", "name", "café", "cafe\u0301"], "UNICODE_HEADER_COLLISION"),
    (["id", "source", "name", "email", "contact_email"], "AMBIGUOUS_INPUT_SCHEMA"),
    (["id", "source", "name", "phone", "notification_phone"], "AMBIGUOUS_INPUT_SCHEMA"),
    (["id", "source", "name", "record_id"], "AMBIGUOUS_INPUT_SCHEMA"),
    (["id", "source", "name", "raw__name"], "INVALID_INPUT_SCHEMA"),
    (["id", "source", "name", ""], "INVALID_INPUT_SCHEMA"),
    (["id", "name"], "INVALID_INPUT_SCHEMA"),
    (["source", "name"], "INVALID_INPUT_SCHEMA"),
    (["id", "source"], "INVALID_INPUT_SCHEMA"),
])
@pytest.mark.asyncio
async def test_invalid_header_has_safe_error_report(header, code):
    with pytest.raises(HTTPException) as error:
        await parse_upload(UploadFile(filename="records.csv", file=BytesIO(payload(header, [["SECRET"] * len(header)]))))
    assert error.value.status_code == 422
    assert error.value.report["code"] == code
    assert "SECRET" not in json.dumps(error.value.report)
    assert error.value.report["resolver_version"]


@pytest.mark.parametrize("field,alias", [
    (field, alias) for field, aliases in load_contract()["fields"].items() for alias in aliases
])
def test_every_versioned_alias_resolves(field, alias):
    required = {"source_record_id": "id", "source_system": "source", "name": "name"}
    required[field] = alias
    mapping = resolve_source_schema(list(required.values()))
    assert mapping.fields[field] == alias
    assert mapping.contract_version == "p119_input_v1"


def test_cross_role_alias_ambiguity_is_rejected():
    contract = load_contract()
    contract["fields"]["phone"].append("contact_email")
    with pytest.raises(HTTPException) as error:
        resolve_source_schema(["id", "source", "name", "contact_email"], contract)
    assert error.value.report["code"] == "AMBIGUOUS_INPUT_SCHEMA"


@pytest.mark.asyncio
async def test_explicit_source_id_is_authoritative_and_auxiliary_ids_retained():
    header = ["source_record_id", "record_id", "id", "source", "name"]
    values = ["0001", "snapshot", "auxiliary", "CRM", "Ada"]
    parsed = await parse_upload(UploadFile(filename="records.csv", file=BytesIO(payload(header, [values]))))
    assert parsed.rows[0]["external_id"] == "0001"
    assert parsed.rows[0]["extra_fields"] == {"record_id": "snapshot", "id": "auxiliary"}
    assert parsed.rows[0]["original_fields"] == dict(zip(header, values))


def import_file(client, content):
    response = client.post("/api/v1/mdm/runs", files=[("files", ("records.csv", content, "text/csv"))])
    assert response.status_code == 201, response.text
    return response.json()


def test_api_preserves_raw_strings_normalized_data_and_lineage(client):
    header = ["account_number", "source", "account_holder", "notification_phone", "contact_email",
              "date_of_birth", "CCCD", "unknown_note"]
    values = ["000123", " crm ", "  Nguyễn An  ", "0901234567", " An@Example.test ",
              "01/02/1990", "001234567890", "  giữ nguyên\nvalue  "]
    content = payload(header, [values])
    result = import_file(client, content)
    assert result["profile"]["files"][0]["mapping_report"]["unknown_fields"] == ["unknown_note"]
    with client.session_factory() as db:
        record = db.query(ResolutionRecord).filter_by(run_id=result["run_id"]).one()
        assert record.external_id == "000123"
        assert record.raw_fields["phone"] == "0901234567"
        assert record.raw_fields["government_id"] == "001234567890"
        assert record.raw_fields["name"] == values[2]
        assert record.raw_fields["email"] == values[4]
        assert record.original_fields == dict(zip(header, values))
        assert record.normalized_fields["phone"] == "0901234567"
        assert record.normalized_fields["government_id"] == "001234567890"
        assert record.normalized_fields["dob"] == "1990-02-01"
        assert record.extra_fields == {"unknown_note": values[-1]}
        assert record.mapping_provenance["fields"]["name"] == "account_holder"
        assert record.mapping_provenance["contract_sha256"]
        assert record.lineage["record_id"] == record.id
        assert record.lineage["source_record_id"] == "000123"
        assert record.lineage["source_system"] == "CRM"
        assert record.lineage["source_file"] == "records.csv"
        assert record.lineage["source_row_number"] == 1
        assert record.lineage["source_file_id"] == record.file_id
        snapshot_id = record.id
    other = import_file(client, content)
    with client.session_factory() as db:
        record = db.query(ResolutionRecord).filter_by(run_id=other["run_id"]).one()
        assert record.id != snapshot_id
        assert (record.source_system, record.external_id) == ("CRM", "000123")


@pytest.mark.asyncio
async def test_same_external_id_in_two_sources_is_valid_in_parser():
    content = payload(["id", "source", "name"], [["001", "CRM", "Ada"], ["001", "ERP", "Bob"]])
    parsed = await parse_upload(UploadFile(filename="records.csv", file=BytesIO(content)))
    assert parsed.source_counts == {"CRM": 1, "ERP": 1}


def test_api_same_external_id_across_files_and_sources(client):
    files = [("files", (f"{source}.csv", payload(["id", "source", "name"], [["001", source, "Ada"]]), "text/csv"))
             for source in ("CRM", "ERP")]
    result = client.post("/api/v1/mdm/runs", files=files)
    assert result.status_code == 201, result.text
    with client.session_factory() as db:
        records = db.query(ResolutionRecord).filter_by(run_id=result.json()["run_id"]).all()
        assert len(records) == 2
        assert len({r.id for r in records}) == 2
        assert {r.external_id for r in records} == {"001"}


def test_chunk_upload_uses_same_namespace_validation(client, monkeypatch, tmp_path):
    from masterone.config import get_settings

    from tests.test_mdm.test_upload_sessions import _create_draft, _upload_payload

    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path))
    content = payload(["id", "source", "account_holder"], [["001", "CRM", "Ada"], ["001", "ERP", "Bob"]])
    draft = _create_draft(client, "records.csv", content)
    _upload_payload(client, draft, content)
    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    assert client.post(prefix + "/validate").json()["status"] == "READY"
    committed = client.post(prefix + "/commit")
    assert committed.status_code == 201, committed.text
    assert committed.json()["profile"]["row_count"] == 2
    assert client.post(prefix + "/commit").json()["run_id"] == committed.json()["run_id"]


def test_invalid_mapping_in_draft_persists_error_report(client, monkeypatch, tmp_path):
    from masterone.config import get_settings

    from tests.test_mdm.test_upload_sessions import _create_draft, _upload_payload

    monkeypatch.setattr(get_settings(), "upload_staging_dir", str(tmp_path))
    content = payload(["id", "source", "name", "account_holder"], [["001", "CRM", "Ada", "Ada"]])
    draft = _create_draft(client, "records.csv", content)
    _upload_payload(client, draft, content)
    prefix = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    report = client.post(prefix + "/validate").json()
    assert report["status"] == "ERROR"
    error = report["files"][0]["validation_report"]
    assert isinstance(report["files"][0]["validation_error"], str)
    assert error["code"] == "AMBIGUOUS_INPUT_SCHEMA"
    assert client.post(prefix + "/commit").status_code == 409


def test_duplicate_within_source_rolls_back_import(client):
    content = payload(["id", "source", "name"], [["001", "CRM", "Ada"], ["001", "CRM", "Bob"]])
    response = client.post("/api/v1/mdm/runs", files=[("files", ("records.csv", content, "text/csv"))])
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert response.json()["error_report"]["code"] == "IMPORT_CONSTRAINT_VIOLATION"
    with client.session_factory() as db:
        assert db.query(ResolutionRun).count() == 0
        assert db.query(ResolutionRecord).count() == 0


def test_migration_preserves_records_and_guards_rollback(tmp_path, monkeypatch):
    from alembic import command
    from masterone.mdm.migrate import alembic_config
    from sqlalchemy import MetaData, Table, create_engine, inspect, select

    monkeypatch.delenv("MDM_DATABASE_URL", raising=False)
    url = "sqlite:///" + (tmp_path / "migration.db").as_posix()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0020_phase5_entities_and_edges")
    engine = create_engine(url)
    try:
        metadata = MetaData()
        table = Table("mdm_resolution_records", metadata, autoload_with=engine)
        old = {"id": "snapshot-old", "run_id": "run-old", "file_id": "file-old",
               "external_id": "001", "source_system": "CRM", "entity_type": "PERSON",
               "raw_fields": {"name": "Ada", "phone": "0901234567"},
               "normalized_fields": {"name": "ada"}, "quality_flags": [],
               "extra_fields": {"custom": "kept"}, "benchmark_entity_id": None}
        with engine.begin() as conn:
            conn.execute(table.insert().values(**old))
        command.upgrade(cfg, "head")
        table = Table("mdm_resolution_records", MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            saved = dict(conn.execute(select(table)).mappings().one())
            assert {key: saved[key] for key in old} == old
            assert saved["lineage"] is None  # historical row position is unknown
            assert saved["original_fields"] is None
        with engine.begin() as conn:
            conn.execute(table.update().values(original_fields={"name": "  Ada  "},
                                               mapping_provenance={"version": "kept"}))
        command.downgrade(cfg, "0020_phase5_entities_and_edges")
        table = Table("mdm_resolution_records", MetaData(), autoload_with=engine)
        with engine.connect() as conn:
            saved = dict(conn.execute(select(table)).mappings().one())
            assert {key: saved[key] for key in old} == old
            assert saved["original_fields"] == {"name": "  Ada  "}
            assert saved["mapping_provenance"] == {"version": "kept"}
        command.upgrade(cfg, "head")
        table = Table("mdm_resolution_records", MetaData(), autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(table.insert().values(**{**old, "id": "snapshot-new", "source_system": "ERP"}))
        with pytest.raises(RuntimeError, match="overlapping IDs"):
            command.downgrade(cfg, "0020_phase5_entities_and_edges")
        assert {"lineage", "original_fields", "mapping_provenance"} <= {
            column["name"] for column in inspect(engine).get_columns("mdm_resolution_records")}
        with engine.connect() as conn:
            assert len(conn.execute(select(table)).all()) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize("columns,source", [
    (["record_id", "source", "name", "phone", "email", "address"], "CSV"),
    (["id", "source_system", "full_name", "phone_number", "email_address", "raw_address"], "ERP"),
    (["contact_id", "source_system", "contact_name", "mobile", "email", "mailing_address"], "CRM"),
    (["debtor_code", "source_system", "legal_name", "contact_number", "email", "invoice_address"], "BILL"),
    (["MA_KH", "source_system", "HO_TEN", "DIEN_THOAI", "email", "DIA_CHI"], "ERP"),
])
def test_old_schema_normalization_equivalence(client, columns, source):
    from masterone.mdm.pipeline.standardization import standardize

    values = ["0001", source, "  Ada  ", " 0901234567 ", " Ada@Example.test ", " Ha Noi "]
    result = import_file(client, payload(columns, [values]))
    raw = dict(zip(["name", "phone", "email", "address"], values[2:]))
    expected, flags = standardize({key: value.strip() for key, value in raw.items()}, entity_type="PERSON")
    with client.session_factory() as db:
        record = db.query(ResolutionRecord).filter_by(run_id=result["run_id"]).one()
        assert record.raw_fields == raw
        assert record.normalized_fields == expected
        assert record.quality_flags == flags


@pytest.mark.parametrize("separator", [",", ";", "\t", "|"])
@pytest.mark.asyncio
async def test_existing_delimiters_and_bom_remain_accepted(separator):
    content = ("\ufeff" + separator.join(["id", "source", "name"]) + "\n" +
               separator.join(["0001", "CRM", "Ada"]) + "\n").encode("utf-8")
    parsed = await parse_upload(UploadFile(filename="records.csv", file=BytesIO(content)))
    assert parsed.rows[0]["external_id"] == "0001"


def test_api_missing_mapping_returns_readable_detail_and_structured_report(client):
    content = payload(["id", "source"], [["0001", "CRM"]])
    response = client.post("/api/v1/mdm/runs", files=[("files", ("records.csv", content, "text/csv"))])
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert response.json()["error_report"]["details"]["missing_fields"] == ["name"]


def test_api_duplicate_source_identity_across_files_rolls_back(client):
    files = [("files", (filename, payload(["id", "source", "name"], [row]), "text/csv"))
             for filename, row in (
                 ("first.csv", ["001", "CRM", "Ada"]),
                 ("second.csv", [" 001 ", " crm ", "Bob"]))]
    response = client.post("/api/v1/mdm/runs", files=files)
    assert response.status_code == 422
    assert response.json()["error_report"]["code"] == "IMPORT_CONSTRAINT_VIOLATION"
    with client.session_factory() as db:
        assert db.query(ResolutionRun).count() == 0
        assert db.query(ResolutionRecord).count() == 0


def test_lineage_uses_logical_rows_and_preserves_input_lineage_columns(client):
    header = ["source_record_id", "record_id", "source_system", "source_file",
              "source_row_number", "account_holder", "notification_phone", "cccd", "custom"]
    rows = [
        ["0001", "upstream-snapshot", "CRM", "upstream.csv", "0099", "  Nguyễn\nAn  ",
         " 0901234567 ", "001234567890", "  unknown\nvalue  "],
        ["0002", "another-snapshot", "CRM", "other.csv", "0100", "Bình", "", "", "kept"],
    ]
    result = import_file(client, payload(header, rows))
    with client.session_factory() as db:
        records = db.query(ResolutionRecord).filter_by(run_id=result["run_id"]).order_by(
            ResolutionRecord.external_id).all()
        for number, (record, row) in enumerate(zip(records, rows), start=1):
            assert record.original_fields == dict(zip(header, row))
            assert record.id != row[1]
            assert record.lineage["record_id"] == record.id
            assert record.lineage["source_record_id"] == row[0]
            assert record.lineage["source_file"] == "records.csv"
            assert record.lineage["source_row_number"] == number
            assert record.extra_fields == {"record_id": row[1], "source_file": row[3],
                                           "source_row_number": row[4], "custom": row[8]}
            assert record.mapping_provenance["unknown_fields"] == [
                "record_id", "source_file", "source_row_number", "custom"]
        assert records[0].raw_fields["name"] == rows[0][5]
        assert records[0].raw_fields["phone"] == rows[0][6]
        assert records[0].raw_fields["government_id"] == rows[0][7]


@pytest.mark.parametrize("field,header", [
    ("source_record_id", ["id", "source", "name"]),
    ("source_system", ["id", "source", "name"]),
    ("name", ["id", "source", "account_holder"]),
])
def test_blank_required_value_reports_role_without_leaking_row(client, field, header):
    row = ["PRIVATE-ID", "CRM", "PRIVATE-NAME"]
    row[{"source_record_id": 0, "source_system": 1, "name": 2}[field]] = "  "
    response = client.post("/api/v1/mdm/runs", files=[
        ("files", ("records.csv", payload(header, [row]), "text/csv"))])
    assert response.status_code == 422
    report = response.json()["error_report"]
    assert report["code"] == "INVALID_INPUT_RECORD"
    assert report["details"]["missing_fields"] == [field]
    assert report["details"]["source_row_number"] == 1
    assert "PRIVATE" not in json.dumps(report)


def test_unknown_contract_version_is_rejected():
    with pytest.raises(ValueError, match="Unsupported input contract version"):
        load_contract("p119_input_v999")
