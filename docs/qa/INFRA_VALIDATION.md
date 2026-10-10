# Infrastructure validation — 2026-10-09

## Local Docker isolation

Project `masterone-qa`, Compose `docker-compose.qa.yml`, ignored `.env.qa.local`. Dedicated volumes `masterone-qa_qa_postgres` and `masterone-qa_qa_runtime`. Không tác động `onemaster-postgres`/`onemaster-redis` hoặc production data. Compose config validated bằng `config --quiet`; không xuất environment/DSN/secrets.

| Service | Endpoint | Actual state/evidence |
|---|---|---|
| PostgreSQL16 | 127.0.0.1:15432 | Healthy; schema0025_pipeline_checkpoints; input/results/auth/audit persisted |
| FastAPI | http://127.0.0.1:18001 | Healthy `/ready`; actual authenticated requests and worker publication |
| Production Next | http://127.0.0.1:13000 | Healthy; built standalone, actual Edge tests |
| Worker | no public port | Running and processed jobs, including two-worker/crash tests; no Docker healthcheck |
| Moto5.2.3 | http://127.0.0.1:19000 | Running and real S3 protocol tests; no Docker healthcheck; volatile emulator |

Không ghi “all five healthy”: **3 have healthy checks,2 running/functionally verified**. Final build/activation log, package versions, resource snapshot và13 source/image hash matches ở `artifacts/full-system-20261009/final-runtime/results.json`.

QA backend APP_ENV=test giữ auth/CSRF/capability checks; Next NODE_ENV=production. Production lifespan/schema validation/Secure+HttpOnly+SameSite cookie/logout cũng đã chạy với ASGI HTTPS TestClient và actual local PG/S3 (`production-mode/results.json`), **không phải deployed TLS ingress**. Production startup không tự migrate; operator phải migrate staging đúng target trước.

## PostgreSQL local và Neon thật

Local tests có DB commit/rollback, unique/FK rejection, concurrent imports, idempotent upserts, real40P01 deadlock detection với một transaction rollback toàn bộ, paginated reads, EXPLAIN ANALYZE và pool release. Fixture transaction table có tên UUID, chỉ tạo/drop ở `127.0.0.1:15432/masterone_qa`; không chạy destructive DDL trên cloud.

100 bounded local query samples: P50/P95/P99 **2,764/3,601/5,373ms**; EXPLAIN dùng run-id index và top-N sort; pool checkedout=0 sau test. Đây là sample một read pattern, không phải production SLA hoặc chứng minh mọi query không có N+1. Offset pagination/high-cardinality broad scans chưa đo toàn bộ.

Actual local DB stop/start: `/ready`503, `/health`200 khi outage; pre-ping pool phục hồi và source records giữ nguyên. New engine connect timeout5s, recycle300s. Không dùng catch/fake success cho missing DB.

Neon QA do user cấp: explicit migrations từ empty public schema tới **0025_pipeline_checkpoints**,41 tables; real API→Neon→deterministic pipeline100→preview/export→R2 signed CSV checksum PASS. Pipeline cloud invoked in process, **chưa triển khai independent cloud worker/front-end**.

Bounded Neon read-only pool: concurrency4,9SELECTs total, first connection~5,766s; subsequent min/median/max~1031/1078/2484ms; checkedout=0; transaction isolation `read committed`. Không claim cold-serverless wakeup vì không biết sleep state; chưa test Neon quota exhaustion/network chaos/deadlocks/cloud retry at sustained load. Evidence `database-transactions/results.json`.

## Real R2 và local S3

Real QA R2 endpoint và keys đã valid; dùng bucket user xác nhận dành QA, prefix `masterone-qa/20261009/<uuid>/` riêng. Cleanup chỉ object keys do probe tự tạo; không delete bucket hoặc objects của run/user khác.

Real execution `real-r2-extended/results.json`:

- Multipart11.700.000bytes upload9,453s; Head/content length và streamed SHA verified;179 chunks<=64KiB; presigned Range206 đúng64bytes.
- Parquet1000rows read/write equality + content type.
- Eight objects, concurrency4 read/write.
- Uploaded5MiB multipart part rồi abort; object chưa published.
- Cleanup10 owned objects, post-delete absence verified.

Basic real cloud probe còn xác nhận35-byte object và signed read;100-record output R2 checksum. Local emulator có riêng10,5MB multipart, ranged read,16 concurrent operations và abort. Không thay local PASS thành real R2 PASS.

SDK adapter connect5s/read30s, standard retries **total_max_attempts=3**. Health probe connect/read2s,total attempts1. `exists` chỉ404/NoSuchKey/NotFound là false, auth/network errors propagate. StreamingBody luôn đóng khi read success/failure; presign endpoint tách server `storage:9000` và browser `127.0.0.1:19000`. Real R2 không cần public anonymous bucket URL.

Moto5.1.12 đã gây FlexibleChecksumError với installed SDK;5.2.3 PASS với checksum validation giữ nguyên.17 owned objects đã backup/restore trước đổi emulator; nhiều objects sau đó chưa nằm trong archive lịch sử. **Không recreate/stop Moto trước fresh backup nếu muốn giữ local artifacts**. Emulator interruption test dùng pause/unpause, không recreate.

## Queue, concurrency và recovery

`operations/results.json` có8PASS cases. Hai simultaneous uploads100records mỗi run;12 claim contenders chỉ1owner; expired-owned-attempt recovery, stale heartbeat fencing và2real workers. Killed actual spawned worker child trên synthetic4k job:2attempts,4000records,34628 unique published pairs, không duplicate serving pairs. Worker supervisor xác nhận success qua DB commit, không infer từ process exit.

V2/resume/streaming opt-in 100/1k: publication PROMOTED,15/68checkpoint receipts, exports99/990rows và source coverage100/1000; execute replay chỉ một export. TC1>10k cũng completed:10022records,83139candidates,17153serving pairs,23proposals,~474,531s. Khác legacy engine semantics; không coi counts phải giống legacy. Các cờ đã restore OFF cho runtime cuối; existing run selections vẫn pinned.

Crash test production queue đã thật trên local legacy; unit suite còn kiểm private generation/resume failure points. **Chưa chạy live v2 mid-partition crash/resume trên cloud hoặc deterministic network-loss matrix cho mọi boundary.** Không gọi một successful retry là proof toàn recovery model.

## PG–R2 consistency

Configured source upload error hiện fail502 và transaction rollback; owned attempted source objects được compensate khi file sau invalid. Tests không xóa unrelated keys. Private generation/publication/checksum barriers có code/test; incomplete results không được publish thành serving snapshot trong covered cases.

Missing owned export object: download409, không302to broken object; restore đúng bytes và checksum PASS. Storage pause/readiness503→200 PASS. Unit SDK outage path503 PASS. Orphan cleanup/reconciliation ở mọi crash/DB-commit boundary **chưa có durable service và full live evidence**; output có thể tồn tại trước DB commit. Không có distributed transaction. GateB/F chưa full PASS.

## Configuration và dependencies

Required values dựa code: `MDM_DATABASE_PROFILE`, `MDM_DATABASE_URL` hoặc `MDM_DATABASE_URL_LOCAL`, `AUTH_CSRF_SECRET`, `CORS_ORIGINS`; optional configured storage cần `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. `R2_PRESIGN_ENDPOINT_URL` chỉ khi browser/server endpoints khác. `NEXT_PUBLIC_API_BASE_URL` là build-time frontend config.

Gemini key fallback `_1/_2/_3` works, first nonempty selected, không rotate. `OPENROUTER_API_KEY` absent và new router unwired; không thiếu credential cần hỏi thêm cho Gemini/R2/Neon scope hiện tại. Không tự thêm `DIRECT_URL`, `JWT_SECRET`, `R2_ACCOUNT_ID` khi code không dùng. Redis optional cho subsystems khác, không bắt buộc resolution worker.

Python3.11.9 host/3.11image, Node24.21.0 host/Node24image; Next16.3.6/React19 dependency snapshot. Docker29.8.1/Compose5.5.1. Package versions chính xác nằm final-runtime JSON; host/container FastAPI/Pydantic/boto SDK khác minor do backend lower-bound dependencies. QA extra deps pin ở `scripts/requirements-qa.txt`; release backend lock/digest pin còn P1. Không upgrade major framework tùy tiện trong audit.
