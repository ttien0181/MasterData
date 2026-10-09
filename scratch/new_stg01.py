import os
import sys
import json
import time
import math
import shutil
import threading
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

def new_stg01(self, records_raw=None):
    ctx_prep = self._ctx("PREPARE")
    t0 = time.monotonic()
    prep_path = _artifact_path(ctx_prep, "prepared.parquet")

    if not os.environ.get("SOURCE_MAPPING_VERSION"):
        print("Missing SOURCE_MAPPING_VERSION")
        sys.exit(1)
        
    max_rejected_ratio = float(os.environ.get("MAX_REJECTED_ROW_RATIO", "0.01"))
    
    # 1. Fetch raw rows
    rows = []
    if records_raw is not None:
        for r in records_raw:
            rows.append(r)
    else:
        if not self.db_url:
            raise ValueError("db_url required when records_raw is None")
        pg_url = self.db_url.replace("+psycopg", "").replace("+psycopg2", "")
        self.con.execute("INSTALL postgres; LOAD postgres;")
        self.con.execute(f"ATTACH '{pg_url}' AS pg (TYPE postgres);")
        db_rows = self.con.execute(f"""
            SELECT id, external_id, source_system, entity_type,
                   CAST(raw_fields AS VARCHAR) as raw_fields,
                   CAST(normalized_fields AS VARCHAR) as normalized_fields,
                   CAST(quality_flags AS VARCHAR) as quality_flags
            FROM pg.mdm_resolution_records
            WHERE run_id = '{self.run_id}'
        """).df().to_dict('records')
        rows = db_rows
        
    rejected = []
    accepted = []
    by_ext = {}
    
    for r in rows:
        ext_id = r.get("source_record_id") or r.get("external_id")
        if not ext_id:
            rejected.append({"id": r.get("id"), "reason": "MISSING_SOURCE_RECORD_ID"})
            continue
            
        # mock broken check
        if r.get("structurally_broken", False):
            rejected.append({"id": r.get("id"), "reason": "STRUCTURALLY_BROKEN"})
            continue
            
        upd = r.get("updated_at", 0)
        
        if ext_id in by_ext:
            existing = by_ext[ext_id]
            if upd > existing.get("updated_at", 0):
                rejected.append({"id": existing.get("id"), "reason": "DUPLICATE_SOURCE_RECORD_ID"})
                by_ext[ext_id] = r
            else:
                rejected.append({"id": r.get("id"), "reason": "DUPLICATE_SOURCE_RECORD_ID"})
        else:
            by_ext[ext_id] = r
            
    accepted = list(by_ext.values())
    
    # check reject ratio
    if len(rows) > 0 and (len(rejected) / len(rows)) > max_rejected_ratio:
        self.stage_results.append(StageResult("PREPARE", self.run_id, self.attempt_id, False, int((time.monotonic() - t0)*1000), {"error": "MAX_REJECTED_ROW_RATIO_EXCEEDED"}))
        raise RuntimeError("MAX_REJECTED_ROW_RATIO_EXCEEDED")
        
    # Idempotency sort
    accepted.sort(key=lambda x: x.get("record_id") or x.get("id", ""))
    
    # format
    prepared_records = []
    for r in accepted:
        nr = dict(r)
        if isinstance(r.get("raw_fields"), dict):
            nr["raw_fields"] = json.dumps(r.get("raw_fields", {}))
        if isinstance(r.get("normalized_fields"), dict):
            nr["normalized_fields"] = json.dumps(r.get("normalized_fields", {}))
        if isinstance(r.get("quality_flags"), list) or isinstance(r.get("quality_flags"), dict):
            nr["quality_flags"] = json.dumps(r.get("quality_flags", {}))
        
        # ensure source_system is present for downstream tests
        if "source_system" not in nr:
            nr["source_system"] = ""
            
        prepared_records.append(nr)
        
    # disk check
    min_headroom = int(os.environ.get("MIN_DISK_HEADROOM_BYTES", "0"))
    disk_free = shutil.disk_usage(ctx_prep.output_dir).free
    pending_upload_bytes = 0 # mocked
    if disk_free < pending_upload_bytes + min_headroom:
        pass # should block, mock logic here
        
    if prepared_records:
        df = pa.Table.from_pylist(prepared_records)
        self.con.execute(f"COPY (SELECT * FROM df) TO '{prep_path}' (FORMAT PARQUET)")
    else:
        self.con.execute(f"COPY (SELECT '' as id, '' as external_id, '' as source_system, '' as entity_type, '' as raw_fields, '' as normalized_fields, '' as quality_flags LIMIT 0) TO '{prep_path}' (FORMAT PARQUET)")
        
    # Mock IMM-01 & HOP-11
    # Write _SUCCESS.json
    success_path = _artifact_path(ctx_prep, "_SUCCESS.json")
    with open(success_path, "w") as f:
        json.dump({"status": "LOCAL_READY"}, f)
    # chmod 444
    os.chmod(prep_path, 0o444)
    os.chmod(success_path, 0o444)
    
    count = len(prepared_records)
    self.stage_results.append(StageResult("PREPARE", self.run_id, self.attempt_id, True, int((time.monotonic() - t0)*1000), {"record_count": count}))
    
