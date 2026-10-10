"""Execute HTTP/DB/S3 integration against the isolated Docker QA stack only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
URL = "http://127.0.0.1:18001"
ORIGIN = "http://127.0.0.1:13000"
DB = "postgresql+psycopg://qa:qa-local-only@127.0.0.1:15432/masterone_qa"


def synthetic(count):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["record_id", "source", "name", "phone", "email", "address", "dob", "government_id"])
    writer.writeheader()
    for i in range(count):
        entity = i - 1 if i % 100 == 1 else i
        writer.writerow({"record_id": f"qa-{i}", "source": "CRM" if i % 2 == 0 else "ERP",
            "name": f"Nguyễn Kiểm Thử {entity}", "phone": f"09{entity:08d}",
            "email": f"qa-{entity}@example.com", "address": f"{entity} phố Kiểm Thử, Hà Nội",
            "dob": "1990-01-01", "government_id": f"001090{entity:06d}"})
    return stream.getvalue().encode("utf-8-sig")


class Probe:
    def __init__(self, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(DB, pool_pre_ping=True, connect_args={"connect_timeout": 10})
        self.client = httpx.Client(base_url=URL, headers={"Origin": ORIGIN}, timeout=120, follow_redirects=True)
        self.results = []
        self.latencies = []

    def save(self):
        (self.output / "results.json").write_text(json.dumps({"cases": self.results,
            "requests": self.latencies, "scope": "local Docker PostgreSQL + Moto S3; not real Neon/R2"},
            ensure_ascii=False, indent=2), encoding="utf-8")

    def check(self, name, function):
        start = time.monotonic()
        try:
            details = function() or {}
            result = {"name": name, "status": "PASS", "seconds": time.monotonic() - start, **details}
        except Exception as error:
            # Never serialize exception text, SQL params, DSN, row values or tokens.
            result = {"name": name, "status": "FAIL", "seconds": time.monotonic() - start,
                "error_type": type(error).__name__, "error_code": getattr(error, "qa_code", "ASSERTION_OR_EXECUTION_FAILURE")}
            frames = traceback.extract_tb(error.__traceback__)
            result["error_location"] = [{"file": Path(frame.filename).name, "line": frame.lineno} for frame in frames[-3:]]
        self.results.append(result)
        self.save()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    def request(self, method, path, expected=200, **kwargs):
        start = time.monotonic()
        response = self.client.request(method, path, **kwargs)
        self.latencies.append({"method": method, "path": path, "status": response.status_code,
                               "seconds": time.monotonic() - start})
        if response.status_code != expected:
            error = RuntimeError()
            error.qa_code = f"HTTP_{response.status_code}_EXPECTED_{expected}"
            raise error
        return response

    def login(self):
        credentials = {"email": "qa-steward@example.com", "password": "local-qa-synthetic-password"}
        response = self.client.post("/api/v1/auth/register", json=credentials)
        assert response.status_code in {201, 409}
        # Explicit test fixture role assignment in a dedicated local DB.
        with self.engine.begin() as db:
            db.execute(text("UPDATE users SET role='SENIOR_STEWARD' WHERE email_normalized=:email"),
                       {"email": credentials["email"]})
        payload = self.request("POST", "/api/v1/auth/login", json=credentials).json()
        self.client.headers["X-CSRF-Token"] = payload["csrf_token"]
        assert payload["role"] == "SENIOR_STEWARD"
        return {"role": payload["role"]}

    def upload(self, paths=None, content=None):
        with ExitStack() as stack:
            files = [("files", (p.name, stack.enter_context(p.open("rb")), "text/csv")) for p in paths] if paths else [
                ("files", ("qa.csv", content, "text/csv"))]
            response = self.request("POST", "/api/v1/mdm/runs", expected=201, files=files)
        return response.json()

    def run(self, upload, expected_count, timeout=600):
        run_id = upload["run_id"]
        start = time.monotonic()
        task = self.request(
            "POST", f"/api/v1/mdm/runs/{run_id}/start", expected=202).json()
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            state = self.request("GET", f"/api/v1/mdm/runs/tasks/{task['task_id'] if 'task_id' in task else task['id']}").json()
            if state["status"] in {"COMPLETED", "RETRYABLE_FAILED", "FAILED", "CANCELLED"}:
                break
            time.sleep(1)
        assert state["status"] == "COMPLETED", "Pipeline did not publish a completed snapshot"
        with self.engine.connect() as db:
            row_count = db.execute(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run"), {"run": run_id}).scalar_one()
            run = db.execute(text("SELECT candidate_count,status FROM mdm_resolution_runs WHERE id=:run"), {"run": run_id}).mappings().one()
            pairs = db.execute(text("SELECT count(*) FROM mdm_resolution_pairs WHERE run_id=:run"), {"run": run_id}).scalar_one()
            proposals = db.execute(text("SELECT count(*) FROM mdm_resolution_proposals WHERE run_id=:run"), {"run": run_id}).scalar_one()
            lineage = db.execute(text("SELECT count(*) FROM mdm_resolution_records WHERE run_id=:run AND original_fields IS NOT NULL AND lineage IS NOT NULL"), {"run": run_id}).scalar_one()
        assert row_count == expected_count == upload["profile"]["row_count"] == lineage
        assert run["status"] == "COMPLETED"
        return {"run_id": run_id, "record_count": row_count, "candidate_count": run["candidate_count"],
                "persisted_pairs": pairs, "proposals": proposals, "pipeline_seconds": time.monotonic() - start,
                "raw_and_lineage_present": True}

    def workflow(self, run_id):
        prefix = f"/api/v1/mdm/runs/{run_id}"
        self.request("GET", prefix + "/overview")
        self.request("GET", prefix + "/analytics")
        review = self.request("GET", prefix + "/items/review?limit=25").json()
        merge = self.request("GET", prefix + "/items/merge?limit=25").json()
        self.request("GET", prefix + "/separate-records?q=Kiểm&limit=25")
        # Review the synthetic-only fixture; never autoapprove benchmark labels.
        if review["items"]:
            item = review["items"][0]
            self.request("GET", prefix + f"/items/review/{item['id']}")
            self.request("POST", prefix + "/review-items/transition",
                         json={"item_ids": [item["id"]], "decision": "no_merge"})
        preview = self.request("POST", prefix + "/previews", json={"acknowledge_unreviewed": True}).json()
        preview_id = preview.get("preview_id", preview.get("id"))
        request_id = str(uuid4())
        exported = self.request("POST", prefix + f"/previews/{preview_id}/execute",
                                json={"execute_request_id": request_id})
        replay = self.request("POST", prefix + f"/previews/{preview_id}/execute",
                              json={"execute_request_id": request_id})
        assert exported.content == replay.content
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        membership = [member for row in rows for member in row["source_record_ids"].split("|")]
        with self.engine.connect() as db:
            count = db.execute(text("SELECT record_count FROM mdm_resolution_runs WHERE id=:run"), {"run": run_id}).scalar_one()
            snapshots = db.execute(text("SELECT count(*) FROM mdm_resolution_exports WHERE run_id=:run"), {"run": run_id}).scalar_one()
        assert len(membership) == len(set(membership)) == count
        assert snapshots == 1
        exports = self.request("GET", prefix + "/exports").json()["items"]
        downloaded = self.request("GET", prefix + f"/exports/{exports[0]['id']}/download")
        assert hashlib.sha256(downloaded.content).digest() == hashlib.sha256(exported.content).digest()
        audit = self.request("GET", prefix + "/audit").json()
        return {"run_id": run_id, "export_rows": len(rows), "source_coverage": len(membership),
            "snapshot_count_after_replay": snapshots, "sha256": hashlib.sha256(exported.content).hexdigest(),
            "merge_items": merge.get("total"), "review_items": review.get("total"), "audit_response_present": bool(audit)}

    def negatives(self):
        invalid = {
            "empty": b"", "schema": b"nonsense\n1\n",
            "duplicate": b"record_id,source,name\na,CRM,Test\na,CRM,Test\n",
            "malformed": b'record_id,source,name\na,CRM,"unterminated\n',
            "encoding": b"record_id,source,name\na,CRM,\xff\n",
            "missing_name": b"record_id,source,name\na,CRM,\n",
        }
        with self.engine.connect() as db:
            before = db.execute(text("SELECT count(*) FROM mdm_resolution_runs")).scalar_one()
        for value in invalid.values():
            self.request("POST", "/api/v1/mdm/runs", expected=422,
                         files={"files": ("invalid.csv", value, "text/csv")})
        with self.engine.connect() as db:
            after = db.execute(text("SELECT count(*) FROM mdm_resolution_runs")).scalar_one()
        assert before == after
        self.request("POST", "/api/v1/mdm/runs", expected=403, headers={"X-CSRF-Token": "invalid"},
                     files={"files": ("x.csv", synthetic(2), "text/csv")})
        self.request("OPTIONS", "/api/v1/auth/register", expected=400,
            headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"})
        with httpx.Client(base_url=URL) as anonymous:
            assert anonymous.get("/api/v1/mdm/runs").status_code == 401
        return {"invalid_imports": len(invalid), "atomic_db_rollback": True, "csrf_origin_auth_enforced": True}

    def storage(self):
        from masterone.storage.object_store import R2ObjectStore
        store = R2ObjectStore("http://127.0.0.1:19000", "qa-local-access", "qa-local-secret-only", "masterone-qa")
        prefix = "qa-probes/" + str(uuid4()) + "/"
        keys = []
        content = b"qa-synthetic-content\n" * 500000
        key = prefix + "multipart.bin"
        keys.append(key)
        try:
            stream = io.BytesIO(content)
            start = time.monotonic()
            store.upload_stream(stream, key)
            upload = time.monotonic() - start
            assert not stream.closed and store.exists(key)
            downloaded = store.download_bytes(key)
            assert hashlib.sha256(downloaded).digest() == hashlib.sha256(content).digest()
            with httpx.Client() as http:
                assert http.get(store.presigned_url(key)).content == content
            response = store.client.get_object(Bucket=store.bucket, Key=key, Range="bytes=0-63")
            try:
                assert response["Body"].read() == content[:64]
            finally:
                response["Body"].close()
            def operation(i):
                item = prefix + f"concurrent-{i}"
                keys.append(item)
                store.upload_bytes(str(i).encode(), item, "text/plain")
                assert store.download_bytes(item) == str(i).encode()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(operation, range(16)))
            abort_key = prefix + "aborted.bin"
            multipart = store.client.create_multipart_upload(Bucket=store.bucket, Key=abort_key)
            store.client.upload_part(Bucket=store.bucket, Key=abort_key, UploadId=multipart["UploadId"], PartNumber=1, Body=b"x" * (5 * 1024**2))
            store.client.abort_multipart_upload(Bucket=store.bucket, Key=abort_key, UploadId=multipart["UploadId"])
            assert not store.exists(abort_key)
            return {"multipart_bytes": len(content), "upload_seconds": upload, "checksum_valid": True,
                    "presigned_get": True, "range_read": True, "concurrent_operations": 16,
                    "interrupted_upload_abort": True}
        finally:
            for item in keys:
                assert item.startswith(prefix)
                store.delete(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--datasets", type=Path)
    parser.add_argument("--mode", choices=["smoke", "datasets", "storage"], default="smoke")
    parser.add_argument("--only", help="Exact decoded dataset directory name; optional bounded rerun")
    args = parser.parse_args()
    probe = Probe(args.output)
    try:
        probe.check("health", lambda: {"response": probe.request("GET", "/health").json()})
        if args.mode == "storage":
            probe.check("s3_adapter", probe.storage)
        else:
            login = probe.check("authenticated_steward", probe.login)
            if login["status"] != "PASS":
                return 1
            if args.mode == "smoke":
                probe.check("invalid_ingestion_auth_csrf", probe.negatives)
                for count in (100, 1000, 4000):
                    result = probe.check(f"pipeline_{count}", lambda count=count: probe.run(probe.upload(content=synthetic(count)), count))
                    if count == 100 and result["status"] == "PASS":
                        probe.check("review_preview_export_replay_download", lambda: probe.workflow(result["run_id"]))
                probe.check("s3_adapter", probe.storage)
            else:
                for dataset in sorted(args.datasets.iterdir()):
                    if args.only and dataset.name != args.only:
                        continue
                    inventory = json.loads((dataset / "inventory.json").read_text(encoding="utf-8"))
                    paths = sorted(dataset.glob("*.csv"))
                    probe.check(dataset.name, lambda paths=paths, inventory=inventory: probe.run(
                        probe.upload(paths=paths), inventory["decoded_records"]))
    finally:
        probe.client.close()
        probe.engine.dispose()
        probe.save()
    return int(any(case["status"] == "FAIL" for case in probe.results))


if __name__ == "__main__":
    raise SystemExit(main())
