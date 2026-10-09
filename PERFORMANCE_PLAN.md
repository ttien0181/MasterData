# Performance Improvement Plan — Validate & Commit APIs

## Tổng quan vấn đề

API validate và commit chậm do: double file materialization, standardize() per-row synchronous,
sequential scoring, multiple full-data loads, và O(n²) TF-IDF blocking.

---

## PHASE 1: Quick Wins — Loại bỏ công việc lặp (ETA: 1-2 ngày)

### Task 1.1: Cache assembled file sau validate, reuse trong commit

**File:** `backend/src/masterone/mdm/upload_sessions.py`

**Vấn đề:** `materialize_upload_file()` (line 277) được gọi lại trong `commit_upload_session()` (line 427),
ghép lại toàn bộ file từ parts + SHA-256 verify lần thứ hai — dù validate đã làm xong.

**Giải pháp:**
- Sau validate thành công, KHÔNG xóa assembled file tại `assembled_file_path(session.id, file.id)`.
  Hiện tại file đã được ghi ra disk tại line 284-301 nhưng không ai giữ reference.
- Trong `commit_upload_session()` (line 427), kiểm tra nếu assembled file đã tồn tại VÀ SHA-256
  match với `file.sha256` (đã lưu ở line 300) → dùng trực tiếp, skip materialize.
- Chỉ gọi `materialize_upload_file()` nếu file chưa tồn tại (fallback cho edge case).
- Xóa assembled file trong `cleanup_upload_session_storage()` (line 442) như bình thường.

**Code thay đổi (pseudo):**
```python
# upload_sessions.py, trong commit_upload_session(), thay line 427:
def _get_or_materialize(db, session, file):
    path = assembled_file_path(session.id, file.id)
    if path.exists() and file.sha256:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == file.sha256:
            return path
    return materialize_upload_file(db, session, file)

paths = [(f.filename, _get_or_materialize(db, session, f)) for f in upload_files]
```

**Impact:** Giảm ~40-50% thời gian commit cho file lớn.

---

### Task 1.2: Sửa duplicate detection — dùng Python set thay row-by-row fallback

**File:** `backend/src/masterone/mdm/upload_sessions.py` (lines 346-361)

**Vấn đề:** Khi batch 10,000 rows chứa duplicate, fallback insert từng row một.

**Giải pháp:** Dùng Python `set()` track duplicate trước khi insert vào SQLite:
```python
# Thay thế logic try/except IntegrityError:
seen = set()
for original in current_files:
    ...
    for row in _iter_normalized(stream, file.filename):
        eid = row["external_id"]
        if eid in seen:
            raise ValueError(f"ID {eid} bị lặp trong cùng phiên")
        seen.add(eid)
        batch.append((eid,))
        ...
        if len(batch) >= 10000:
            ids_db.executemany("INSERT INTO ids (external_id) VALUES (?)", batch)
            batch.clear()
```
Loại bỏ hoàn toàn SQLite `try/except IntegrityError` fallback.

**Impact:** Worst case validate giảm từ O(n) xuống O(1) per batch.

---

## PHASE 2: Tối ưu Commit — Standardize song song (ETA: 2-3 ngày)

### Task 2.1: Chuyển standardize() sang multiprocessing

**File:** `backend/src/masterone/mdm/resolution_import.py` (lines 258-275)

**Vấn đề:** `standardize()` gọi per-row synchronous trong single thread.
Hàm này (471 dòng trong `pipeline/standardization.py`) thực hiện Unicode normalization,
Vietnamese name tokenization, phone normalization, address parsing, ward resolution — rất nặng.

**Giải pháp:**
```python
# resolution_import.py — thay vòng lặp line 258-275
from concurrent.futures import ProcessPoolExecutor
import os

def _standardize_batch(rows):
    """Worker function cho multiprocessing."""
    results = []
    for row in rows:
        raw = row["raw_fields"]
        normalized, quality_flags = standardize(raw, entity_type=row["entity_type"])
        results.append({**row, "normalized_fields": normalized, "quality_flags": quality_flags})
    return results

# Trong create_resolution_run_from_streams(), thay for loop:
WORKER_BATCH = 500
workers = min(os.cpu_count() or 4, 4)
raw_rows = list(_iter_normalized(stream, filename))

with ProcessPoolExecutor(max_workers=workers) as pool:
    chunks = [raw_rows[i:i+WORKER_BATCH] for i in range(0, len(raw_rows), WORKER_BATCH)]
    for result_batch in pool.map(_standardize_batch, chunks):
        batch = []
        for row in result_batch:
            batch.append({
                "run_id": run.id, "file_id": file_row.id,
                "external_id": row["external_id"],
                "source_system": row["source_system"],
                "entity_type": row["entity_type"],
                "benchmark_entity_id": row["benchmark_entity_id"],
                "raw_fields": row["raw_fields"],
                "normalized_fields": row["normalized_fields"],
                "quality_flags": row["quality_flags"],
                "extra_fields": row["extra_fields"],
            })
        db.execute(insert(ResolutionRecord), batch)
```

**Lưu ý quan trọng:**
- Kiểm tra `standardize()` và `address_mapping.resolve_ward()` có pickle-safe không
  (không giữ DB connection, file handle, hay module-level mutable state).
- Nếu `resolve_ward()` dùng in-memory lookup dict → mỗi worker sẽ tự load.
- Fallback: nếu multiprocessing phức tạp, dùng `ThreadPoolExecutor` trước — GIL giới hạn
  nhưng vẫn nhanh hơn nếu có I/O trong standardize.

**Impact:** Giảm 60-75% thời gian commit (bottleneck chính).

---

### Task 2.2: Loại bỏ double file hash trong commit

**File:** `backend/src/masterone/mdm/resolution_import.py` (lines 249-253)

**Vấn đề:** SHA-256 hash toàn bộ file trước khi parse — nhưng `file.sha256` đã có sẵn từ
`materialize_upload_file()` (line 300 upload_sessions.py).

**Giải pháp:** Truyền `sha256` đã biết vào `create_resolution_run_from_streams()`:
```python
# Thay đổi signature:
def create_resolution_run_from_streams(db, files, owner_user_id, name=None,
                                        upload_session_id=None,
                                        known_hashes=None):
    ...
    for i, (original_name, stream) in enumerate(files):
        if known_hashes and i < len(known_hashes):
            digest = known_hashes[i]
        else:
            file_hash = hashlib.sha256()
            stream.seek(0)
            while block := stream.read(1024 * 1024):
                file_hash.update(block)
            digest = file_hash.hexdigest()
```

Trong `commit_upload_session()`:
```python
hashes = [f.sha256 for f in upload_files]
run, profile = create_resolution_run_from_streams(
    db, streams, owner_user_id, session.name,
    upload_session_id=session.id, known_hashes=hashes)
```

**Impact:** Loại bỏ 1 lần đọc full file (~vài giây cho file 100MB+).

---

## PHASE 3: Tối ưu Pipeline — Scoring & Blocking (ETA: 3-5 ngày)

### Task 3.1: Parallel score_pair với ProcessPoolExecutor

**Files:**
- `backend/src/masterone/mdm/pipeline/stage_runner.py` (lines 280-295)
- `backend/src/masterone/mdm/resolution_service.py` (lines 31-39)

**Vấn đề:** `score_pair()` gọi tuần tự trong single-threaded loop.
Mỗi call gồm JaroWinkler, Levenshtein, phone/email comparison — CPU-intensive.

**Giải pháp cho stage_runner.py:**
```python
from concurrent.futures import ProcessPoolExecutor

def _score_chunk(chunk, by_id, frequencies, negative_pairs, policy_version, run_id):
    """Score a chunk of candidate pairs — runs in worker process."""
    from masterone.mdm.pipeline.matching import score_pair
    import hashlib
    results = []
    for c in chunk:
        left_id, right_id, reasons = c["left_id"], c["right_id"], c["reasons"]
        result = score_pair(by_id[left_id], by_id[right_id], set(reasons),
                           frequencies, negative_pairs=negative_pairs)
        if result["decision"] == "INSUFFICIENT_EVIDENCE":
            continue
        pair_id = hashlib.sha256(
            f"{run_id}:{left_id}:{right_id}:{policy_version}".encode()
        ).hexdigest()
        results.append({"id": pair_id, "left_id": left_id, "right_id": right_id,
                        "blocking_reasons": reasons, **result})
    return results

# Trong STG-04 SCORE, thay vòng lặp line 281-294:
SCORE_CHUNK = 5000
all_candidates = []
for batch in reader.iter_batches(batch_size=50000):
    all_candidates.extend(batch.to_pylist())

chunks = [all_candidates[i:i+SCORE_CHUNK]
          for i in range(0, len(all_candidates), SCORE_CHUNK)]

with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 4)) as pool:
    futures = [pool.submit(_score_chunk, chunk, by_id, frequencies,
                           negative_pairs, policy_version, self.run_id)
               for chunk in chunks]
    for f in futures:
        scored_pairs.extend(f.result())
```

**Lưu ý:** `by_id` dict phải serialize được (dict of dicts → OK).
Nếu dataset lớn, overhead pickle qua process boundary có thể đáng kể →
benchmark với `shared_memory` hoặc `ThreadPoolExecutor` nếu cần.

Áp dụng tương tự cho `_compute()` trong `resolution_service.py`.

**Impact:** Giảm 50-75% thời gian SCORE stage.

---

### Task 3.2: Load records 1 lần, reuse giữa stages

**File:** `backend/src/masterone/mdm/pipeline/stage_runner.py`

**Vấn đề:** ALL records được load từ DuckDB 3 lần riêng biệt:
- Line 224-226: TF-IDF blocking (to_dict + JSON parse)
- Line 267-275: Scoring (to_dict + JSON parse, chỉ candidate records)
- Line 345-352: Clustering (to_dict + JSON parse, toàn bộ)

**Giải pháp:**
```python
# Thêm vào run_all(), sau STG-02 NORMALIZE:
self._all_records = self.con.execute(
    "SELECT id, external_id, source_system, entity_type, "
    "raw_fields, normalized_fields, quality_flags FROM norm1"
).df().to_dict('records')
for r in self._all_records:
    r["raw_fields"] = json.loads(r["raw_fields"]) if r["raw_fields"] else {}
    r["normalized_fields"] = json.loads(r["normalized_fields"]) if r["normalized_fields"] else {}
    if isinstance(r.get("quality_flags"), str):
        r["quality_flags"] = json.loads(r["quality_flags"]) if r["quality_flags"] else {}
self._by_id = {r["id"]: r for r in self._all_records}

# STG-03: dùng self._all_records cho tfidf_recs
# STG-04: lọc by_id = {k: self._by_id[k] for k in candidate_ids}
# STG-06: dùng self._by_id trực tiếp
```

**Impact:** Giảm ~30% thời gian pipeline tổng, giảm memory churn.

---

### Task 3.3: Giới hạn TF-IDF "all" partition size

**File:** `backend/src/masterone/mdm/pipeline/tfidf_blocking.py`

**Vấn đề:** Records thiếu DOB hoặc province rơi vào partition key `"all"`.
`sim_block = batch * matrix.T` là O(n²) trong mỗi partition.
Nếu partition "all" có 5,000 records → 12.5M similarity computations.

**Giải pháp:**
```python
MAX_PARTITION_SIZE = 3000

for key, indices in partitions.items():
    if len(indices) > MAX_PARTITION_SIZE:
        # Sub-partition bằng first character of name hoặc random split
        random.shuffle(indices)
        for i in range(0, len(indices), MAX_PARTITION_SIZE):
            sub = indices[i:i+MAX_PARTITION_SIZE]
            # process sub-partition...
    else:
        # process partition bình thường...
```

**Alternative tốt hơn:** Dùng `sklearn.neighbors.NearestNeighbors` với cosine metric:
```python
from sklearn.neighbors import NearestNeighbors
nn = NearestNeighbors(n_neighbors=TOP_K, metric='cosine', algorithm='brute', n_jobs=-1)
nn.fit(matrix)
distances, indices = nn.kneighbors(matrix)
```
Tận dụng multi-core và tránh explicit batching.

**Impact:** Ngăn blowup O(n²) khi dữ liệu thiếu DOB/province nhiều.

---

## PHASE 4: Tối ưu Semantic & DB I/O (ETA: 1-2 ngày)

### Task 4.1: Parallel Gemini API calls

**File:** `backend/src/masterone/mdm/pipeline/semantic_address.py`

**Vấn đề:** Gọi Gemini API synchronous, batch 10, có `time.sleep(0.5)` retry.

**Giải pháp đơn giản nhất:**
```python
from concurrent.futures import ThreadPoolExecutor

# Thay vì gọi tuần tự từng batch:
with ThreadPoolExecutor(max_workers=5) as pool:
    futures = [pool.submit(_call_gemini_sync, batch) for batch in batches]
    results = [f.result() for f in futures]
```

**Impact:** Giảm 60-80% thời gian semantic enrichment.

---

### Task 4.2: Tăng INSERT_BATCH_SIZE

**File:** `backend/src/masterone/mdm/resolution_import.py` (line 25, 273-274)

**Vấn đề:** `INSERT_BATCH_SIZE = 1000` — mỗi batch 1 DB round-trip.

**Giải pháp:** Tăng lên 5000-10000:
```python
INSERT_BATCH_SIZE = 5000
```

Hoặc dùng PostgreSQL COPY cho throughput tối đa:
```python
from io import StringIO
import csv

def _copy_records(db, records):
    buf = StringIO()
    writer = csv.writer(buf)
    for r in records:
        writer.writerow([r["run_id"], r["file_id"], ...])
    buf.seek(0)
    raw_conn = db.get_bind().raw_connection()
    with raw_conn.cursor() as cur:
        cur.copy_from(buf, 'resolution_records', sep=',', columns=[...])
    raw_conn.commit()
```

**Impact:** Giảm 50-70% thời gian DB insert.

---

## Tóm tắt thứ tự ưu tiên

| Phase | Task | Effort   | Impact                     | Risk       |
|-------|------|----------|----------------------------|------------|
| 1     | 1.1  | 30 min   | -50% commit I/O            | Thấp       |
| 1     | 1.2  | 30 min   | -90% validate worst case   | Thấp       |
| 2     | 2.1  | 1 ngày   | -70% commit CPU            | Trung bình |
| 2     | 2.2  | 15 min   | -vài giây per file         | Thấp       |
| 3     | 3.1  | 1 ngày   | -60% score time            | Trung bình |
| 3     | 3.2  | 2 giờ    | -30% pipeline              | Thấp       |
| 3     | 3.3  | 4 giờ    | Ngăn O(n²) blowup          | Trung bình |
| 4     | 4.1  | 4 giờ    | -70% semantic              | Thấp       |
| 4     | 4.2  | 1 giờ    | -60% DB insert             | Thấp       |

---

## Hướng dẫn cho AI Agent thực hiện

1. **Chạy test trước mỗi thay đổi:** `pytest backend/tests/test_mdm/` để có baseline
2. **Mỗi task = 1 commit riêng** để dễ rollback
3. **Task 1.1 và 1.2** an toàn nhất, nên làm trước
4. **Task 2.1 (multiprocessing)** cần kiểm tra `standardize()` và `address_mapping`
   có pickle-safe không — nếu không, dùng `ThreadPoolExecutor` trước
5. **Task 3.1** cần benchmark overhead pickle `by_id` qua process boundary
6. **Không thay đổi API contract** — tất cả response schema giữ nguyên
7. **Thêm timing log** cho mỗi phase:
   ```python
   import time, logging
   t0 = time.monotonic()
   # ... operation ...
   logging.info("phase_name took %.2fs", time.monotonic() - t0)
   ```
8. **Test với dataset nhỏ (100 rows), trung bình (5000), và lớn (50000)** để verify improvement

---

# PART 2 — Kế hoạch sửa pipeline MDM (branch `feature/feature_4`)

> Phần này bổ sung cho PHASE 1–4 ở trên. PHASE 1–4 nhắm vào luồng validate/commit; PART 2 nhắm vào pipeline `StageRunner` (CLUSTER và SCORE). Hai phần độc lập, làm theo thứ tự task bên dưới.

## 0. Bối cảnh và ràng buộc (đọc trước khi sửa)

- **Mục tiêu:** giảm thời gian chạy pipeline ở quy mô ~10k record, giữ **đầu ra không đổi** (decisions, clusters, review cases, candidate set).
- **Nguồn tham chiếu:** branch `origin/NguyenThanhNam`. Nam đã đo CLUSTER 60.17s → 1.69s trên TC1 (10.022 record) bằng incident-edge index. Các số này là số của Nam, **chưa được đo lại trên feature_4** — phải tự đo (xem mục 4).
- **Không port nguyên khối** từ Nam. Nhánh streaming/checkpoint/job_queue/generation_staging của Nam chưa ổn định (timeout 300s, rollback HTTP 500). Chỉ port các thay đổi được chỉ định trong plan này.
- **Commit trạng thái hiện tại trước khi sửa.** Working tree của feature_4 đang có ~633 dòng thay đổi chưa commit ở `stage_runner.py`, `tfidf_blocking.py`, `semantic_address.py`, `upload_sessions.py`, v.v. Chạy `git status`; nếu còn thay đổi chưa commit, commit thành một commit riêng ("chore: snapshot working tree before PART 2") để có điểm quay lại sạch. Không xóa thay đổi này.
- **Môi trường Windows:** cần cài `duckdb`, `pandas`, `pyarrow`. `splink` là tùy chọn — 7 test trong `test_scoring_engine.py` sẽ fail nếu thiếu, không phải lỗi logic.
- Chạy test từ thư mục `backend/`: `python -m pytest tests/test_mdm -q`. Baseline đã đo: **302 passed, 1 skipped, 0 failed, ~35s**. Mọi thay đổi phải giữ nguyên con số này (hoặc tăng khi thêm test mới).

---

## TASK P2-1: Thêm incident-edge index cho guard trong CLUSTER

**Vấn đề:** `build_proposals()` trong [clustering.py](backend/src/masterone/mdm/pipeline/clustering.py) gọi `run_cluster_guards(..., pairs=pairs)` ở **mỗi lần thử merge** (dòng ~49–52). `_shared_value_guard` trong [cluster_guards.py](backend/src/masterone/mdm/pipeline/cluster_guards.py) (dòng ~108–128) lọc toàn bộ `pairs` để tìm các cạnh nối hai cụm. Kết quả: độ phức tạp ~ O(số_merge × số_pair), và quét toàn bộ snapshot mỗi lần.

**Vì sao đúng:** một cạnh "inter-pair" (nối cụm trái và cụm phải) bắt buộc có ít nhất một đầu mút thuộc **cụm nhỏ hơn**. Vì vậy chỉ cần lấy các cạnh liên quan đến các record của cụm nhỏ hơn là đủ — đây là tập cha của inter-pairs, và `_shared_value_guard` vẫn tự lọc đúng.

**Thay đổi cần làm:**

1. Trước vòng `for pair in match_edges`, dựng index (giữ vị trí gốc để thứ tự không đổi):

```python
from collections import defaultdict

incident_pairs = defaultdict(list)
for position, pair in enumerate(pairs):
    incident_pairs[pair["left_id"]].append((position, pair))
    incident_pairs[pair["right_id"]].append((position, pair))
```

2. Thay lời gọi `run_cluster_guards(...)` (dòng ~49–52) bằng bản chỉ truyền cạnh liên quan:

```python
smaller = min((members[left_root], members[right_root]), key=len)
relevant = {position: item for rid in smaller for position, item in incident_pairs[rid]}
guard_violations = run_cluster_guards(
    frozenset(members[left_root]), frozenset(members[right_root]),
    by_id, negative_pairs=negative_pairs,
    pairs=[relevant[position] for position in sorted(relevant)],
)
```

3. **Không** đổi chữ ký `build_proposals`, không đổi `blocked_links`, không đổi `edge_order`.

**Điều kiện chấp nhận (bắt buộc):**
- `pytest tests/test_mdm -q` vẫn **302 passed**.
- Với cùng input (10k record, seed cố định), so sánh `proposals`, `review_cases`, `risky_pairs` trước và sau khi sửa. Serialize bằng `json.dumps(..., sort_keys=True)` rồi so sánh SHA-256 — **phải bằng nhau**. Nếu khác, dừng lại và tìm nguyên nhân; không chấp nhận.
- Đo thời gian stage CLUSTER trước/sau; ghi vào mục 4.

**Rủi ro:** thấp. Nếu `_shared_value_guard` về sau thay đổi cách lấy inter-pairs, index này phải được cập nhật cùng.

---

## TASK P2-2: Sửa pickle `by_id` lặp lại trong SCORE (ProcessPool)

**Vấn đề:** trong [stage_runner.py](backend/src/masterone/mdm/pipeline/stage_runner.py), đoạn SCORE (~dòng 314–317) làm:

```python
futures = [pool.submit(_score_chunk, chunk, by_id, frequencies, negative_pairs, policy_version, self.run_id)
           for chunk in chunks]
```

Mỗi `submit` pickle **toàn bộ `by_id`** (tất cả record của candidate) một lần cho mỗi chunk. Với N chunk, đó là N lần serialize dữ liệu lớn — chi phí này có thể lớn hơn lợi ích của song song.

**Thay đổi cần làm:** truyền dữ liệu dùng chung một lần cho mỗi worker bằng `initializer`:

```python
_WORKER_STATE = {}

def _init_score_worker(by_id, frequencies, negative_pairs, policy_version, run_id):
    _WORKER_STATE.update(by_id=by_id, frequencies=frequencies, negative_pairs=negative_pairs,
                         policy_version=policy_version, run_id=run_id)

def _score_chunk_worker(chunk):
    s = _WORKER_STATE
    return _score_chunk(chunk, s["by_id"], s["frequencies"], s["negative_pairs"],
                        s["policy_version"], s["run_id"])
```

Và khởi tạo pool:

```python
with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 4),
                         initializer=_init_score_worker,
                         initargs=(by_id, frequencies, negative_pairs, policy_version, self.run_id)) as pool:
    futures = [pool.submit(_score_chunk_worker, chunk) for chunk in chunks]
```

Giữ nguyên thứ tự `for f in futures:` để kết quả có thứ tự xác định.

**Lưu ý:** trên Windows, `ProcessPoolExecutor` dùng `spawn`, nên `_init_score_worker` và `_score_chunk_worker` phải là hàm cấp module (không lambda, không closure). `_score_chunk` gốc đã đúng điều kiện này.

**Điều kiện chấp nhận:**
- `scored_pairs` trước và sau khớp hoàn toàn (so sánh danh sách đã sort theo `id`).
- `pytest tests/test_mdm -q` vẫn 302 passed.
- Đo thời gian stage SCORE trước/sau. Nếu không nhanh hơn (hoặc chậm hơn) ở ~10k, **giữ nguyên bản cũ** và ghi kết quả vào mục 4.

---

## TASK P2-3 (tùy chọn, làm sau khi P2-1 và P2-2 đã đo): loại bỏ chuyển đổi Parquet dư thừa trong DECIDE

**Hiện trạng:** feature_4 đã bỏ parse-back trong DECIDE (giữ dict Python) — đây là phần tốt, giữ nguyên. Chỉ cân nhắc thêm nếu profiling cho thấy DECIDE vẫn chiếm >15% thời gian. Không làm nếu chưa có profile.

---

## KHÔNG LÀM trong PART 2 (ghi lại để tránh làm nhầm)

- **Không port** `ParquetSnapshot`, `DecisionStore`, `ScoringEngine`, `CandidateEngine`, streaming, checkpoint, job_queue, generation_staging từ Nam. Đây là kiến trúc lớn, chưa ổn định, và cần thay đổi nhiều file phụ thuộc. Nếu sau này cần scale >100k, lập plan riêng.
- **Không thêm composite blocking keys** (`name+year`, `surname+year`, v.v.) cho tới khi có số đo recall cho thấy feature_4 đang bỏ sót candidate thật. Thêm khi chưa đo sẽ làm tăng số candidate cần chấm điểm và có thể làm chậm SCORE.
- **Không đổi** chữ ký hàm công khai, schema API, hoặc format output.

---

## 4. Đo và ghi kết quả (bắt buộc cho mỗi task)

1. Tạo dataset ~10k record bằng script có seed cố định, lưu vào `scratch/` (không commit).
2. Chạy pipeline **trước khi sửa** và lưu: thời gian từng stage (`stage_results`), SHA-256 của `pairs`/`proposals`/`review_cases`.
3. Áp dụng task, chạy lại, so sánh hash và thời gian.
4. Ghi bảng vào cuối file này theo mẫu:

| Task | Stage | Trước (s) | Sau (s) | Hash khớp | Test mdm | Quyết định |
|------|-------|-----------|---------|-----------|----------|------------|
| P2-1 | CLUSTER | 0.686 | 0.515 | Có | 302 passed | Áp dụng |
| P2-2 | SCORE   | 9.344 | 3.467 | Có | 302 passed | Áp dụng |

Số của Nam (CLUSTER 60.17s → 1.69s) chỉ là tham chiếu, không được dùng thay cho số đo của chính bạn.

---

## 5. Quy tắc commit và kiểm thử

- **Một task = một commit.** Commit message dạng `perf(mdm): <mô tả ngắn>`.
- Mỗi commit phải: (a) `pytest tests/test_mdm -q` xanh, (b) hash đầu ra khớp (trừ khi task cố ý thay đổi đầu ra — khi đó phải ghi rõ trong message và dừng để người duyệt).
- Thứ tự: **P2-1 → P2-2 → (P2-3 nếu cần)**. P2-1 an toàn nhất và có lợi lớn nhất.
- Dừng và báo cáo nếu: hash không khớp, test fail không liên quan đến `splink`, hoặc số đo không cải thiện.
