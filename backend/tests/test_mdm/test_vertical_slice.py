from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from masterone.db.base import Base
from masterone.db.session import create_database
from masterone.mdm.models import AuditEvent, ImportBatch, SourceRecord
from sqlalchemy import select

CRM = b"source_record_id,name,email,phone,extra\nC-1,Acme,hello@acme.test,123,do-not-return\n"
ERP = b"source_record_id,name,email,phone\nE-9,Acme,hello@acme.test,999\n"


def import_pair(client, crm=CRM, erp=ERP):
    response = client.post("/api/v1/mdm/imports", files={
        "crm_file": ("crm.csv", BytesIO(crm), "text/csv"),
        "erp_file": ("erp.csv", BytesIO(erp), "text/csv"),
    })
    return response


def test_import_candidates_decision_and_golden_draft(client):
    imported = import_pair(client)
    assert imported.status_code == 201, imported.text
    payload = imported.json()
    assert payload["candidate_count"] == 1
    candidates = client.get(f"/api/v1/mdm/imports/{payload['import_id']}/candidates").json()
    candidate = candidates[0]
    assert candidate["decision"] == "pending"
    assert candidate["left"]["raw_fields"]["email"] == "hello@acme.test"
    assert "extra" not in str(candidate)
    candidate_id = candidate["id"]

    assert client.put(f"/api/v1/mdm/candidates/{candidate_id}/golden-record", json={"fields": {}}).status_code == 409
    assert client.patch(f"/api/v1/mdm/candidates/{candidate_id}/decision", json={"decision": "accepted"}).status_code == 200
    chosen = candidate["left"]
    draft_path = f"/api/v1/mdm/candidates/{candidate_id}/golden-record"
    incomplete = client.put(draft_path, json={"fields": {
        "name": {"value": chosen["raw_fields"]["name"], "source_record_id": chosen["id"]},
        "email": {"value": chosen["raw_fields"]["email"], "source_record_id": chosen["id"]},
    }})
    assert incomplete.status_code == 422
    draft = client.put(draft_path, json={"fields": {
        "name": {"value": chosen["raw_fields"]["name"], "source_record_id": chosen["id"]},
        "email": {"value": chosen["raw_fields"]["email"], "source_record_id": chosen["id"]},
        "phone": {"value": chosen["raw_fields"]["phone"], "source_record_id": chosen["id"]},
    }})
    assert draft.status_code == 200, draft.text
    assert draft.json()["fields"]["email"]["source_record_id"] == chosen["id"]
    audit = client.get(f"/api/v1/mdm/candidates/{candidate_id}/golden-record").json()
    assert audit["state"] == "draft"
    with client.session_factory() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.candidate_id == candidate_id)).all()
        crm_record = session.scalar(select(SourceRecord).where(SourceRecord.source_record_id == "C-1"))
        assert len(events) == 2
        assert crm_record.raw_fields["email"] == "hello@acme.test"


def test_invalid_csv_does_not_create_partial_import(client):
    response = import_pair(client, b"wrong,name\n1,Acme\n", ERP)
    assert response.status_code == 422
    malformed = import_pair(client, b"source_record_id,name,email\nC-1,Acme\n", ERP)
    assert malformed.status_code == 422


def test_row_limit_does_not_persist_partial_import(client):
    rows = "source_record_id,name\n" + "".join(f"C-{index},Customer {index}\n" for index in range(5001))
    response = import_pair(client, rows.encode(), ERP)
    assert response.status_code == 413
    with client.session_factory() as session:
        assert session.query(ImportBatch).count() == 0


def test_review_transitions_are_enforced(client):
    payload = import_pair(client).json()
    candidate = client.get(f"/api/v1/mdm/imports/{payload['import_id']}/candidates").json()[0]
    route = f"/api/v1/mdm/candidates/{candidate['id']}/decision"
    assert client.patch(route, json={"decision": "needs_review"}).status_code == 200
    assert client.patch(route, json={"decision": "accepted"}).status_code == 200
    assert client.patch(route, json={"decision": "rejected"}).status_code == 409


def test_database_initializes_missing_parent_directory():
    with TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary_directory:
        database_path = Path(temporary_directory) / "nested" / "mdm.db"
        engine, _ = create_database(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(engine)
        assert database_path.exists()
        engine.dispose()

