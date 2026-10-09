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

def stg_01_prepare(self, records_raw=None):
    # Just to draft the STG-01 code block
    ctx_prep = self._ctx("PREPARE")
    t0 = time.monotonic()
    
    # Require SOURCE_MAPPING_VERSION
    if not os.environ.get("SOURCE_MAPPING_VERSION"):
        print("Missing SOURCE_MAPPING_VERSION")
        sys.exit(1)
        
    # read env vars
    PREPARE_BATCH_ROWS = int(os.environ.get("PREPARE_BATCH_ROWS", "100000"))
    PREPARE_FILE_TARGET_ROWS = int(os.environ.get("PREPARE_FILE_TARGET_ROWS", "5000000"))
    MAX_REJECTED_ROW_RATIO = float(os.environ.get("MAX_REJECTED_ROW_RATIO", "0.01"))
    MAX_PENDING_UPLOAD_FILES = int(os.environ.get("MAX_PENDING_UPLOAD_FILES", "64"))
    MAX_PENDING_UPLOAD_BYTES = int(os.environ.get("MAX_PENDING_UPLOAD_BYTES", str(20 * 1024**3)))
    MAX_ACTIVE_R2_TRANSFERS = int(os.environ.get("MAX_ACTIVE_R2_TRANSFERS", "16"))
    R2_MULTIPART_CONCURRENCY = int(os.environ.get("R2_MULTIPART_CONCURRENCY", "8"))
    MIN_DISK_HEADROOM_BYTES = int(os.environ.get("MIN_DISK_HEADROOM_BYTES", "0"))
    
    # ... logic here ...
