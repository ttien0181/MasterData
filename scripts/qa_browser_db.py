"""Verify synthetic real-browser golden lineage, source coverage and split replay."""
import argparse
import csv
import io
import json
from pathlib import Path

from qa_live_system import Probe
from sqlalchemy import text

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path("artifacts/full-system-20261009/browser-db"))
parser.add_argument("--browser", type=Path, default=Path("artifacts/full-system-20261009/browser-golden-source-final"))
args = parser.parse_args()
output = args.output
browser_path = args.browser
browser_result = json.loads((browser_path / "results.json").read_text(encoding="utf-8"))
run_id = browser_result["run_id"]
probe = Probe(output)
probe.check("login", probe.login)


def validate():
    prefix = f"/api/v1/mdm/runs/{run_id}"
    with probe.engine.connect() as db:
        records = {row["id"]: row for row in db.execute(text("SELECT id,external_id,raw_fields,original_fields,lineage FROM mdm_resolution_records WHERE run_id=:run"),
                                                       {"run": run_id}).mappings()}
        exports = list(db.execute(text("SELECT id,parent_export_id,row_count FROM mdm_resolution_exports WHERE run_id=:run ORDER BY created_at,id"),
                                  {"run": run_id}).mappings())
        split = db.execute(text("SELECT * FROM mdm_resolution_split_events WHERE run_id=:run"), {"run": run_id}).mappings().one()
        events = list(db.execute(text("SELECT member_record_ids,golden_fields FROM mdm_resolution_merge_events WHERE export_id=:export"),
                                 {"export": exports[0]["id"]}).mappings())
        previews = db.execute(text("SELECT id,execute_request_id,choices FROM mdm_resolution_previews WHERE export_id=:export"),
                              {"export": exports[0]["id"]}).mappings().one()
        actions = db.execute(text("SELECT action,count(*) FROM mdm_resolution_audit_events WHERE run_id=:run GROUP BY action"),
                             {"run": run_id}).all()
    assert len(records) == 7 and all(row["original_fields"] and row["lineage"] for row in records.values())
    assert [row["row_count"] for row in exports] == [5, 6]
    assert exports[1]["parent_export_id"] == exports[0]["id"] and len(events) == 2
    golden_values_checked = 0
    phone_selection_checked = False
    for event in events:
        for field, value in event["golden_fields"].items():
            source = records[value["source_record_id"]]
            assert source["id"] in event["member_record_ids"] and value["value"] == source["raw_fields"][field]
            golden_values_checked += 1
        if {records[rid]["external_id"] for rid in event["member_record_ids"]} == {"a", "b"}:
            assert records[event["golden_fields"]["phone"]["source_record_id"]]["external_id"] == browser_result["golden_phone_choice_external_id"]
            phone_selection_checked = True
    assert phone_selection_checked
    for export in exports:
        downloaded = probe.request("GET", prefix + f"/exports/{export['id']}/download")
        rows = list(csv.DictReader(io.StringIO(downloaded.content.decode("utf-8-sig"))))
        coverage = [rid for row in rows for rid in row["source_record_ids"].split("|")]
        assert len(rows) == export["row_count"] and len(coverage) == len(set(coverage)) == 7
        # CSV contract exports original external IDs, while DB event lineage
        # uses internal UUIDs. This fixture deliberately has unique IDs a..g.
        assert set(coverage) == {row["external_id"] for row in records.values()}
    probe.request("POST", prefix + f"/previews/{previews['id']}/execute",
                  json={"execute_request_id": previews["execute_request_id"]})
    with probe.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM mdm_resolution_exports WHERE run_id=:run"), {"run": run_id}).scalar_one() == 2
    # A stale pair revision must never create another decision/audit event.
    with probe.engine.connect() as db:
        pair = db.execute(text("SELECT id,revision FROM mdm_resolution_pairs WHERE run_id=:run "
                               "AND user_decision IN ('ACCEPTED','REJECTED') ORDER BY id LIMIT 1"),
                          {"run": run_id}).mappings().one()
    probe.request("POST", prefix + f"/review-pairs/{pair['id']}/decision", expected=409,
                  json={"decision": "merge", "revision": pair["revision"] - 1})
    # A confirmed golden edit invalidates an already-created preview, without
    # publishing a new CSV. Clear only this fixture's temporary override.
    proposal_id = probe.request("GET", prefix + "/items/merge").json()["items"][0]["id"]
    golden_path = prefix + f"/proposals/{proposal_id}/golden-record"
    assert probe.request("GET", golden_path).json()["steward_override"] == {}
    fresh = probe.request("POST", prefix + "/previews", json={"choices": previews["choices"], "acknowledge_unreviewed": True}).json()
    try:
        probe.request("PUT", golden_path, json={"fields": {"phone": "0901112223"}})
        assert probe.request("GET", golden_path).json()["steward_override"] == {"phone": "0901112223"}
        probe.request("POST", prefix + f"/previews/{fresh['preview_id']}/execute", expected=409,
                      json={"execute_request_id": "qa-stale-golden"})
    finally:
        probe.request("PUT", golden_path, json={"fields": {}})
    with probe.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM mdm_resolution_exports WHERE run_id=:run"), {"run": run_id}).scalar_one() == 2
    return {"run_id": run_id, "source_records": 7, "initial_export_rows": 5, "split_export_rows": 6,
            "golden_values_checked": golden_values_checked, "manual_phone_source_preserved": True,
            "source_coverage_unique": True, "split_parent_linked": True, "preview_replay_no_duplicate": True,
            "stale_pair_denied": True, "golden_edit_invalidates_preview": True,
            "fixture_override_cleared": True, "audit_action_counts_before_override_probe": dict(actions)}


try:
    probe.check("browser_source_golden_export_split_invariants", validate)
finally:
    probe.save()
    probe.client.close()
    probe.engine.dispose()
raise SystemExit(int(any(case["status"] != "PASS" for case in probe.results)))
