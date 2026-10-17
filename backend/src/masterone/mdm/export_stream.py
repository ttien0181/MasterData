"""Bounded export artifacts. Publication follows coverage and remote verification."""

import csv
import ctypes
import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_

from masterone.config import get_settings
from masterone.mdm.engine_adapter import EngineResultAdapter
from masterone.mdm.models import (
    ResolutionAuditEvent,
    ResolutionExport,
    ResolutionPair,
    ResolutionProposal,
    ResolutionRecord,
    ResolutionReviewCase,
    ResolutionRun,
)
from masterone.storage.object_store import LocalObjectStore
from masterone.storage.r2 import get_store

COLUMNS = ("master_record_id", "full_name", "phone_number", "email_address", "raw_address",
           "source_record_ids", "source_systems", "record_count", "resolution_status", "review_pending")
log = logging.getLogger(__name__)


def rss_peak():
    if os.name == "nt":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    import sys
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


class Budget:
    def __init__(self, directory, settings):
        self.directory, self.settings = Path(directory), settings
        self.start = time.monotonic()
        self.metrics = {"peak_rss_bytes": rss_peak(), "disk_peak_bytes": 0,
                        "spill_peak_bytes": 0, "queue_depth": 0,
                        "backpressure_wait_seconds": 0.0, "upload_duration_seconds": 0.0,
                        "batch_rows": settings.export_batch_rows, "stage_duration_seconds": {}}

    def check(self):
        disk = sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
        spill = sum(p.stat().st_size for p in self.directory.glob("index*"))
        peak = rss_peak()
        self.metrics["disk_peak_bytes"] = max(disk, self.metrics["disk_peak_bytes"])
        self.metrics["spill_peak_bytes"] = max(spill, self.metrics["spill_peak_bytes"])
        self.metrics["peak_rss_bytes"] = max(peak, self.metrics["peak_rss_bytes"])
        if disk > self.settings.export_disk_bytes:
            raise HTTPException(507, "Export disk budget exceeded")
        if self.settings.export_max_rss_bytes and peak > self.settings.export_max_rss_bytes:
            raise HTTPException(507, "Export RSS budget exceeded")
        if time.monotonic() - self.start > self.settings.export_timeout_seconds:
            raise HTTPException(504, "Export time budget exceeded")


def batches(query, size, budget=None):
    """Keyset pagination releases each ORM batch before fetching the next."""
    model = query.column_descriptions[0]["entity"]
    last = None
    while True:
        page = query.filter(model.id > last) if last is not None else query
        rows = page.order_by(model.id).limit(size).all()
        if not rows:
            break
        last = rows[-1].id
        for row in rows:
            yield row
        del row, rows
        if budget:
            budget.check()


def proposal_record_batches(db, query, size, budget=None):
    """Load source rows once per bounded proposal batch instead of once per group."""
    proposals = batches(query, size, budget)
    while True:
        page = []
        source_count = 0
        for proposal in proposals:
            page.append(proposal)
            source_count += len(proposal.member_record_ids)
            if source_count >= size:
                break
        if not page:
            break
        member_ids = {rid for proposal in page for rid in proposal.member_record_ids}
        records = {}
        ordered_ids = sorted(member_ids)
        for start in range(0, len(ordered_ids), 500):
            for row in db.query(ResolutionRecord).filter(
                    ResolutionRecord.id.in_(ordered_ids[start:start + 500])).all():
                records[row.id] = row
        for proposal in page:
            members = proposal.member_record_ids
            if (len(set(members)) != len(members) or
                    any(rid not in records or records[rid].run_id != proposal.run_id for rid in members)):
                raise HTTPException(409, "Nguồn bị thiếu hoặc trùng trong nhóm sẽ gộp")
            yield proposal, sorted((records[rid] for rid in members),
                                   key=lambda row: (row.source_system, row.external_id, row.id))


@contextmanager
def index(settings):
    root = Path(settings.export_artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="build-", dir=root) as directory:
        budget = Budget(directory, settings)
        state = sqlite3.connect(str(Path(directory) / "index.sqlite"))
        try:
            state.executescript("""
                PRAGMA journal_mode=OFF; PRAGMA temp_store=FILE; PRAGMA cache_size=-2048;
                CREATE TABLE owner(id TEXT PRIMARY KEY, proposal TEXT);
                CREATE TABLE nodes(id TEXT PRIMARY KEY, parent TEXT, pending INTEGER DEFAULT 0);
                CREATE TABLE cases(id TEXT PRIMARY KEY);
            """)
            yield state, budget, Path(directory)
        finally:
            state.close()


def find(state, rid):
    state.execute("INSERT OR IGNORE INTO nodes(id,parent) VALUES (?,?)", (rid, rid))
    root = rid
    while True:
        parent = state.execute("SELECT parent FROM nodes WHERE id=?", (root,)).fetchone()[0]
        if parent == root:
            break
        root = parent
    while rid != root:
        parent = state.execute("SELECT parent FROM nodes WHERE id=?", (rid,)).fetchone()[0]
        state.execute("UPDATE nodes SET parent=? WHERE id=?", (root, rid))
        rid = parent
    return root


def review_index(db, run_id, state, budget):
    def union(members):
        if members:
            root = find(state, members[0])
            for rid in members[1:]:
                state.execute("UPDATE nodes SET parent=? WHERE id=?", (root, find(state, rid)))
    size = budget.settings.export_batch_rows
    for proposal in batches(db.query(ResolutionProposal).filter(
            ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(("AUTO_READY", "REVIEW_APPROVED"))), size, budget):
        union(proposal.member_record_ids)
    for case in batches(db.query(ResolutionReviewCase).filter_by(run_id=run_id, status="OPEN"), size, budget):
        union(case.member_record_ids)
        if case.member_record_ids:
            state.execute("INSERT OR IGNORE INTO cases VALUES (?)", (case.member_record_ids[0],))
    state.execute("CREATE TABLE roots(id TEXT PRIMARY KEY)")
    for (rid,) in state.execute("SELECT id FROM cases"):
        state.execute("INSERT OR IGNORE INTO roots VALUES (?)", (find(state, rid),))
    for (rid,) in state.execute("SELECT id FROM nodes"):
        pending = bool(state.execute("SELECT 1 FROM roots WHERE id=?", (find(state, rid),)).fetchone())
        state.execute("UPDATE nodes SET pending=? WHERE id=?", (int(pending), rid))
    budget.check()
    return state.execute("SELECT COUNT(*) FROM roots").fetchone()[0]


def readiness(db, run_id, choices, state, budget):
    from masterone.mdm.resolution_items import _choice_state, _is_blocked, _options, _valid_choices
    choices = choices or {}
    blockers, missing = [], []
    count = 0
    size = budget.settings.export_batch_rows

    def blocker(proposal, kind, message, fields=None):
        result = {"item_id": proposal.id, "kind": kind, "message": message,
                  "label": proposal.golden_fields.get("fields", {}).get("name", {}).get("value", "Khách hàng")}
        if fields:
            result["fields"] = fields
        return result

    for proposal, source_rows in proposal_record_batches(db, db.query(ResolutionProposal).filter(
            ResolutionProposal.run_id == run_id, ResolutionProposal.status.in_(("AUTO_READY", "REVIEW_APPROVED"))), size, budget):
        count += 1
        if len(proposal.member_record_ids) > budget.settings.export_max_group_records:
            raise HTTPException(507, "Export group budget exceeded")
        overlap = False
        for rid in set(proposal.member_record_ids):
            try:
                state.execute("INSERT INTO owner VALUES (?,?)", (rid, proposal.id))
            except sqlite3.IntegrityError:
                overlap = True
        if overlap:
            blockers.append(blocker(proposal, "OVERLAP", "Cụm có bản ghi trùng với cụm khác; cần kiểm tra lại."))
        options = _options(source_rows)
        selected = choices.get(proposal.id, {})
        if not isinstance(selected, dict) or _valid_choices(selected, options) != selected:
            raise HTTPException(422, "Lựa chọn nguồn của item không hợp lệ")
        _, fields = _choice_state(options, "AUTO" if proposal.golden_fields.get("_default_unselected")
                                  else proposal.origin, selected)
        fields = [f for f in fields if f not in proposal.golden_fields.get("_golden_override", {})]
        if fields:
            missing.append({"item_id": proposal.id, "fields": fields})
            blockers.append(blocker(proposal, "MISSING_CHOICES", "Chọn giá trị cho các trường khác nhau.", fields))
    pending = review_index(db, run_id, state, budget) + db.query(ResolutionProposal).filter_by(
        run_id=run_id, status="REVIEW_REQUIRED").count()
    staged = 0
    for proposal in batches(db.query(ResolutionProposal).filter_by(run_id=run_id, status="REVIEW_STAGED_CONFLICT"), size, budget):
        staged += 1
        blockers.append(blocker(proposal, "STAGED_CONFLICT", "Cụm có cặp Không gộp; cần tách cụm an toàn."))
    state.execute("CREATE TABLE blocked(id TEXT PRIMARY KEY)")
    for pair in batches(db.query(ResolutionPair).filter(
            ResolutionPair.run_id == run_id,
            or_(ResolutionPair.pair_decision.in_(("NON_MATCH", "NOT_MERGEABLE")),
                ResolutionPair.user_decision.in_(("REJECTED", "NON_MATCH")))), size, budget):
        left = state.execute("SELECT proposal FROM owner WHERE id=?", (pair.left_record_id,)).fetchone()
        right = state.execute("SELECT proposal FROM owner WHERE id=?", (pair.right_record_id,)).fetchone()
        if left and left == right and _is_blocked(pair):
            state.execute("INSERT OR IGNORE INTO blocked VALUES (?)", left)
    for (pid,) in state.execute("SELECT id FROM blocked ORDER BY id"):
        blockers.append(blocker(db.get(ResolutionProposal, pid), "NEGATIVE_LINK",
                                "Cụm chứa cặp đã xác nhận Không gộp; cần tách trước khi xuất."))
    return {"merge_count": count, "pending_review_count": pending, "staged_conflict_count": staged,
            "incomplete_count": len(missing), "incomplete_items": missing,
            "blockers": blockers, "can_export": not blockers}


def export_readiness(db, run_id, submitted_choices=None):
    settings = get_settings()
    with index(settings) as (state, budget, _):
        return readiness(db, run_id, submitted_choices, state, budget)


def artifact_store(settings, key):
    return LocalObjectStore(settings.export_artifact_dir) if key.startswith("local:") else get_store(settings)


def artifact_key(key):
    return key.removeprefix("local:")


def digest_chunks(chunks):
    digest, size = hashlib.sha256(), 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def file_chunks(path, budget):
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            budget.check()
            yield chunk


def create_export(db, run_id, actor_user_id, acknowledge_unreviewed,
                  submitted_choices=None, *, commit=True, export_id=None, verified_readiness=None):
    settings = get_settings()
    started = time.monotonic()
    # Same run lock is held through verification/publication, including preview execution.
    db.query(ResolutionRun).filter_by(id=run_id).with_for_update().one()
    request_hash = hashlib.sha256(json.dumps([acknowledge_unreviewed, submitted_choices or {}],
                                 sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if export_id:
        existing = db.get(ResolutionExport, export_id)
        if existing:
            if existing.run_id != run_id or existing.actor_user_id != actor_user_id:
                raise HTTPException(409, "Export identity conflict")
            audit = db.query(ResolutionAuditEvent).filter_by(run_id=run_id, action="CSV_EXPORTED").yield_per(
                settings.export_batch_rows)
            receipt = next((row.details for row in audit if row.details.get("export_id") == export_id), {})
            if receipt.get("request_hash") != request_hash:
                raise HTTPException(409, "Export request identity reused with different choices")
            return existing
    export_id = export_id or str(uuid4())
    with index(settings) as (state, budget, directory):
        try:
            ready = verified_readiness if verified_readiness is not None else readiness(
                db, run_id, submitted_choices, state, budget)
            budget.metrics["stage_duration_seconds"]["readiness"] = time.monotonic() - started
            if not ready["can_export"] or (ready["pending_review_count"] and not acknowledge_unreviewed):
                raise HTTPException(409, "Còn cụm cần xử lý hoặc xác nhận trước khi xuất CSV")
            state.execute("CREATE TABLE covered(id TEXT PRIMARY KEY)")
            path = directory / "snapshot.csv"
            row_count = covered_count = 0
            parquet = None
            buffer = []
            if settings.export_parquet_enabled:
                import pyarrow as pa
                import pyarrow.parquet as pq
                schema = pa.schema([(name, pa.string()) for name in COLUMNS])
                parquet = pq.ParquetWriter(directory / "snapshot.parquet", schema)
            write_start = time.monotonic()
            try:
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
                    writer.writeheader()

                    def append(gid, records, values, status, pending=False):
                        nonlocal row_count, covered_count
                        for record in records:
                            try:
                                state.execute("INSERT INTO covered VALUES (?)", (record.id,))
                            except sqlite3.IntegrityError as exc:
                                raise HTTPException(409, "Nguồn bị trùng trong bản xuất") from exc
                        covered_count += len(records)
                        row = dict(zip(COLUMNS, (gid, values.get("name", ""), values.get("phone", ""),
                            values.get("email", ""), values.get("address", ""),
                            "|".join(sorted(r.external_id for r in records)),
                            "|".join(sorted({r.source_system for r in records})), len(records), status,
                            "true" if pending else "false"), strict=True))
                        writer.writerow(row)
                        row_count += 1
                        if parquet:
                            buffer.append({k: str(v) if v is not None else "" for k, v in row.items()})
                            if len(buffer) >= settings.export_batch_rows:
                                parquet.write_table(pa.Table.from_pylist(buffer, schema=schema))
                                buffer.clear()
                        if row_count % settings.export_batch_rows == 0:
                            stream.flush()
                            budget.check()

                    from masterone.mdm.resolution_items import FIELDS
                    for proposal, records in proposal_record_batches(db, db.query(ResolutionProposal).filter(
                            ResolutionProposal.run_id == run_id,
                            ResolutionProposal.status.in_(("AUTO_READY", "REVIEW_APPROVED"))),
                            settings.export_batch_rows, budget):
                        if not records:
                            raise HTTPException(409, "Nguồn bị thiếu trong bản xuất")
                        by_id = {r.id: r for r in records}
                        gf = EngineResultAdapter.golden(proposal.golden_fields)["fields"]
                        overrides = proposal.golden_fields.get("_golden_override", {})
                        selected = (submitted_choices or {}).get(proposal.id, {})
                        values = {}
                        for field in FIELDS:
                            if field in overrides:
                                values[field] = overrides[field]
                            elif field in selected:
                                values[field] = str(by_id[selected[field]].raw_fields.get(field, "")).strip()
                            elif field in gf:
                                values[field] = gf[field].get("value", "") or ""
                            else:
                                values[field] = next((str(by_id[rid].raw_fields.get(field, "")).strip()
                                    for rid in proposal.member_record_ids
                                    if str(by_id[rid].raw_fields.get(field, "")).strip()), "")
                        append(proposal.id, records, values, "MERGED")
                    merged_source_count = covered_count
                    for record in batches(db.query(ResolutionRecord).filter_by(run_id=run_id), settings.export_batch_rows, budget):
                        if not state.execute("SELECT 1 FROM covered WHERE id=?", (record.id,)).fetchone():
                            pending = state.execute("SELECT pending FROM nodes WHERE id=?", (record.id,)).fetchone()
                            append(record.id, [record], record.raw_fields, "UNMERGED", pending and pending[0])
                    stream.flush()
                    os.fsync(stream.fileno())
                if parquet and buffer:
                    parquet.write_table(pa.Table.from_pylist(buffer, schema=schema))
                    buffer.clear()
            finally:
                if parquet:
                    parquet.close()
            budget.check()
            expected = db.query(ResolutionRecord).filter_by(run_id=run_id).count()
            if covered_count != expected:
                raise HTTPException(409, "Bản xuất chưa bao phủ toàn bộ bản ghi đầu vào")
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                expected_rows = expected - merged_source_count + ready["merge_count"]
                if tuple(reader.fieldnames) != COLUMNS or expected_rows != row_count:
                    raise HTTPException(409, "Export schema or row count mismatch")
            budget.metrics["stage_duration_seconds"]["write_validate"] = time.monotonic() - write_start
            store = get_store(settings)
            local = store is None
            if local:
                store = LocalObjectStore(settings.export_artifact_dir)
            artifacts = {}
            upload_start = time.monotonic()
            for file in (path, directory / "snapshot.parquet"):
                if not file.exists():
                    continue
                if file.suffix == ".parquet":
                    with pq.ParquetFile(file) as metadata:
                        if metadata.metadata.num_rows != row_count or metadata.schema_arrow != schema:
                            raise HTTPException(409, "Parquet schema or row count mismatch")
                checksum, size = digest_chunks(file_chunks(file, budget))
                # Content keys make interrupted retries safe, even before DB commit.
                key = f"exports/{run_id}/{export_id}/{checksum}{file.suffix}"
                content_type = "text/csv; charset=utf-8" if file.suffix == ".csv" else "application/vnd.apache.parquet"
                if hasattr(store, "upload_export_file"):
                    store.upload_export_file(file, key, content_type, lambda _: budget.check())
                else:
                    store.upload_file(file, key, content_type)
                def remote_chunks():
                    for chunk in store.iter_chunks(key):
                        budget.check()
                        yield chunk
                if digest_chunks(remote_chunks()) != (checksum, size):
                    raise HTTPException(502, "Export upload checksum mismatch")
                artifacts[file.suffix] = {"key": key, "sha256": checksum, "bytes": size}
            budget.metrics["upload_duration_seconds"] = time.monotonic() - upload_start
            budget.metrics["stage_duration_seconds"]["upload_verify"] = budget.metrics["upload_duration_seconds"]
            budget.metrics["stage_duration_seconds"]["total"] = time.monotonic() - started
            details = {"export_id": export_id, "row_count": row_count, "source_record_count": covered_count,
                       "request_hash": request_hash,
                       "merged_group_count": ready["merge_count"], "coverage": "PASS",
                       "columns": list(COLUMNS), "artifacts": artifacts, "resources": budget.metrics}
            store.write_manifest(f"exports/{run_id}/{export_id}/complete.json", details)
            snapshot = ResolutionExport(id=export_id, run_id=run_id, actor_user_id=actor_user_id,
                csv_bytes=None, r2_key=("local:" if local else "") + artifacts[".csv"]["key"],
                row_count=row_count, merged_group_count=ready["merge_count"],
                pending_review_count=ready["pending_review_count"])
            db.add(snapshot)
            db.add(ResolutionAuditEvent(run_id=run_id, actor_user_id=actor_user_id,
                                       action="CSV_EXPORTED", details=details))
            db.commit() if commit else db.flush()
            log.info("export_complete", extra={"export_id": export_id, "resources": budget.metrics})
            return snapshot
        except Exception as exc:
            db.rollback()
            log.error("export_failed", extra={"export_id": export_id, "error_type": type(exc).__name__,
                                               "resources": budget.metrics})
            raise
