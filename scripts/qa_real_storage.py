"""Small, owned-prefix real R2 probes. No data from users/datasets or LLM calls."""
import hashlib
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from masterone.storage.object_store import R2ObjectStore

values = dotenv_values(ROOT / ".env.qa")
store = R2ObjectStore(values["R2_ENDPOINT_URL"], values["R2_ACCESS_KEY_ID"], values["R2_SECRET_ACCESS_KEY"], values["R2_BUCKET"])
prefix = "masterone-qa/20261009/" + str(uuid4()) + "/"
keys, results = [], []
out = ROOT / "artifacts/full-system-20261009/real-r2-extended"
out.mkdir(exist_ok=False)


def check(name, fn):
    started = time.monotonic()
    try:
        item = {"name": name, "status": "PASS", **(fn() or {})}
    except Exception as error:
        item = {"name": name, "status": "FAIL", "error_type": type(error).__name__,
                "error_code": getattr(error, "response", {}).get("Error", {}).get("Code")}
    item["seconds"] = time.monotonic() - started
    results.append(item)
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(item), flush=True)


def multipart():
    key = prefix + "multipart.bin"
    keys.append(key)
    content = b"synthetic-qa\n" * 900000
    start = time.monotonic()
    stream = io.BytesIO(content)
    store.upload_stream(stream, key)
    duration = time.monotonic() - start
    assert not stream.closed and store.exists(key)
    head = store.client.head_object(Bucket=store.bucket, Key=key)
    assert head["ContentLength"] == len(content)
    digest = hashlib.sha256()
    chunks = 0
    for chunk in store.iter_chunks(key, 65536):
        assert len(chunk) <= 65536
        digest.update(chunk)
        chunks += 1
    assert digest.digest() == hashlib.sha256(content).digest()
    with urlopen(Request(store.presigned_url(key), headers={"Range": "bytes=0-63"}), timeout=30) as response:
        assert response.status == 206 and response.read() == content[:64]
    return {"bytes": len(content), "upload_seconds": duration, "stream_chunks": chunks,
            "checksum": True, "content_length": True, "presigned_ranged_read": True}


def parquet():
    key = prefix + "synthetic.parquet"
    keys.append(key)
    data = pa.table({"id": list(range(1000)), "value": ["synthetic"] * 1000})
    stream = io.BytesIO()
    pq.write_table(data, stream)
    store.upload_bytes(stream.getvalue(), key, "application/vnd.apache.parquet")
    loaded = pq.read_table(io.BytesIO(store.download_bytes(key)))
    assert loaded.equals(data)
    head = store.client.head_object(Bucket=store.bucket, Key=key)
    assert head["ContentType"] == "application/vnd.apache.parquet"
    return {"rows": 1000, "read_write_equal": True, "content_type": True}


def concurrent():
    def operation(number):
        key = prefix + f"concurrent-{number}.txt"
        keys.append(key)
        content = str(number).encode()
        store.upload_bytes(content, key, "text/plain")
        assert store.download_bytes(key) == content
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(operation, range(8)))
    return {"objects": 8, "concurrency": 4}


def abort():
    key = prefix + "aborted.bin"
    upload_id = store.client.create_multipart_upload(Bucket=store.bucket, Key=key)["UploadId"]
    try:
        store.client.upload_part(Bucket=store.bucket, Key=key, UploadId=upload_id, PartNumber=1, Body=b"x" * (5 * 1024**2))
    finally:
        store.client.abort_multipart_upload(Bucket=store.bucket, Key=key, UploadId=upload_id)
    assert not store.exists(key)
    return {"uploaded_part_bytes": 5 * 1024**2, "aborted_upload_not_published": True}


try:
    for name, fn in (("real_r2_multipart_stream_checksum_range", multipart), ("real_r2_parquet", parquet),
                     ("real_r2_concurrent_read_write", concurrent), ("real_r2_interrupted_upload_abort", abort)):
        check(name, fn)
finally:
    def cleanup():
        for key in keys:
            assert key.startswith(prefix)
            store.delete(key)
        assert all(not store.exists(key) for key in keys)
        return {"owned_objects_removed": len(keys)}
    check("owned_prefix_cleanup", cleanup)
    store.client.close()
raise SystemExit(int(any(item["status"] == "FAIL" for item in results)))
