"""Two simultaneous real preview executes publish exactly one owned export."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from qa_live_system import Probe, synthetic
from sqlalchemy import text

probe = Probe(Path("artifacts/full-system-20261009/double-submit"))
probe.check("login", probe.login)


def execute():
    run = probe.run(probe.upload(content=synthetic(100)), 100)
    prefix = f"/api/v1/mdm/runs/{run['run_id']}"
    preview = probe.request("POST", prefix + "/previews", json={"acknowledge_unreviewed": True}).json()
    request_id = str(uuid4())
    def post(_):
        return probe.request("POST", prefix + f"/previews/{preview['preview_id']}/execute",
                             json={"execute_request_id": request_id}).content
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, range(2)))
    assert responses[0] == responses[1]
    with probe.engine.connect() as db:
        exports = db.execute(text("SELECT count(*) FROM mdm_resolution_exports WHERE run_id=:run"), {"run": run["run_id"]}).scalar_one()
        merges = db.execute(text("SELECT count(*) FROM mdm_resolution_merge_events WHERE run_id=:run"), {"run": run["run_id"]}).scalar_one()
        audits = db.execute(text("SELECT count(*) FROM mdm_resolution_audit_events WHERE run_id=:run AND action='PREVIEW_EXECUTED'"), {"run": run["run_id"]}).scalar_one()
    assert exports == merges == audits == 1
    return {"run_id": run["run_id"], "simultaneous_requests": 2, "unique_exports": exports,
            "merge_events": merges, "execute_audit_events": audits, "responses_equal": True,
            "sha256": hashlib.sha256(responses[0]).hexdigest()}


try:
    probe.check("concurrent_preview_execute_idempotency", execute)
finally:
    probe.save()
    probe.client.close()
    probe.engine.dispose()
raise SystemExit(int(any(case["status"] != "PASS" for case in probe.results)))
