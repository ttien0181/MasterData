"""Overview figures come from the stored run summary, which writes keep current."""

import io

from masterone.mdm.models import ResolutionProposal, ResolutionRunSummary
from masterone.mdm.resolution_import import create_resolution_run_from_streams
from sqlalchemy import event

CSV = (b"record_id,source,name,phone,email,address\n"
       b"ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n"
       b"TWO,ERP,Ada Lovelace,0901234567,ada@example.test,Ha Noi\n")


def _create_run(client, owner_id):
    with client.session_factory() as db:
        run, _ = create_resolution_run_from_streams(db, [("people.csv", io.BytesIO(CSV))], owner_id)
        return run.id


def test_commit_stores_the_source_counts_of_the_new_run(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    run_id = _create_run(client, owner_id)
    with client.session_factory() as db:
        assert db.get(ResolutionRunSummary, run_id).source_counts == {"CRM": 1, "ERP": 1}


def test_overview_is_served_without_reading_the_records_table(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    run_id = _create_run(client, owner_id)
    client.get(f"/api/v1/mdm/runs/{run_id}/overview")  # first read stores the payload
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get(f"/api/v1/mdm/runs/{run_id}/overview")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert response.json()["source_counts"] == {"CRM": 1, "ERP": 1}
    assert not [statement for statement in statements if "mdm_resolution_records" in statement]


def test_a_write_to_proposals_refreshes_the_review_figures(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    run_id = _create_run(client, owner_id)
    assert client.get(f"/api/v1/mdm/runs/{run_id}/overview").json()["counts"]["manual"] == 0
    with client.session_factory.begin() as db:
        db.add(ResolutionProposal(id="manual-1", run_id=run_id, member_record_ids=["ONE", "TWO"],
                                  golden_fields={}, status="REVIEW_REQUIRED", revision=1))
    assert client.get(f"/api/v1/mdm/runs/{run_id}/overview").json()["counts"]["manual"] == 1


def test_a_run_without_a_stored_summary_gets_one_on_first_read(client):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    run_id = _create_run(client, owner_id)
    with client.session_factory.begin() as db:
        db.query(ResolutionRunSummary).filter_by(run_id=run_id).delete()
    response = client.get(f"/api/v1/mdm/runs/{run_id}/overview")
    assert response.status_code == 200
    assert response.json()["source_counts"] == {"CRM": 1, "ERP": 1}
    with client.session_factory() as db:
        assert db.get(ResolutionRunSummary, run_id) is not None
