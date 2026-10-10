# Runbook — Windows + Docker, QA only

## Scope và trạng thái

Repo `C:\VinUni\Dự án nhóm\P-119`, branch `qa/full-system-20261009`. Chạy PowerShell tại root repo. Local project `masterone-qa` đang running; không cần bootstrap lại hoặc xóa volumes. Báo cáo chính [FULL_SYSTEM_AUDIT](FULL_SYSTEM_AUDIT.md), decision [PRODUCTION_READINESS](PRODUCTION_READINESS.md).

Không dùng developer `.env` để chạy bulk QA. Không in secrets, DSN, full Compose config hoặc container Env. Các scripts tests inject isolated SQLite/no-provider environment; cloud scripts chỉ đọc ignored `.env.qa` và explicit QA config. Backend migrations production phải explicit, không chạy destructive migrations/reset.

## Prerequisites

Docker Desktop/Compose, Python3.11 và existing `.venv`, Node24 và existing frontend packages. Runtime/package versions đã lưu `artifacts/full-system-20261009/final-runtime/results.json`. Không yêu cầu WSL. QA extras: `pip install -r scripts/requirements-qa.txt` chỉ khi thiếu; backend dev dependencies ở `backend/pyproject.toml`, frontend dùng `npm ci` khi cần. Không tự reinstall/upgrade môi trường đang dùng chỉ để resume.

## Environment files

`.env.qa.local` chỉ cho local Compose, có random `AUTH_CSRF_SECRET`. **Không overwrite file đang có.** Nếu tạo máy QA mới:

```powershell
if (Test-Path -LiteralPath '.env.qa.local') { throw 'File exists; preserve its contents.' }
$qaBytes = New-Object byte[] 48
$qaRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$qaRng.GetBytes($qaBytes)
$qaRng.Dispose()
$qaAuthSecret = [Convert]::ToBase64String($qaBytes)
Set-Content -LiteralPath '.env.qa.local' -Value ('AUTH_CSRF_SECRET=' + $qaAuthSecret) -Encoding ascii
Remove-Variable qaAuthSecret, qaBytes
```

Cloud staging dùng `.env.qa` dựa `.env.qa.example`: `MDM_DATABASE_PROFILE=neon`, `MDM_DATABASE_URL` dedicated QA branch/DB (psycopg URL, sslmode=require), `AUTH_CSRF_SECRET`, R2 endpoint/access/secret/bucket QA, CORS allowlist. Neon URL lấy từ branch connection details; R2 S3 endpoint và bucket-scoped API credentials lấy từ Cloudflare R2. Endpoint là API root, không append bucket/placeholder angle brackets.

LLM chỉ khi có ngân sách: `GEMINI_API_KEY` hoặc `_1/_2/_3`, `GEMINI_MODEL`, `GEMINI_MAX_RETRIES`, `GEMINI_MAX_OUTPUT_TOKENS`; legacy semantic dùng `SEMANTIC_ADDRESS_ENABLED` và max-pairs/timeout. New `LLM_ENABLED/LLM_SEMANTIC_ENABLED/LLM_EXPLANATION_ENABLED` thuộc subsystem riêng, không giả định áp dụng đầy đủ cho legacy routes. No key rotation. `OPENROUTER_API_KEY` chỉ cần cho OpenRouter đã wire, hiện real OR chưa test.

**Budget hiện đã dùng 4/4 requests canary, retries0, shared ceiling0,10USD. Không chạy lại canary hoặc gọi provider thêm khi chưa được cấp ngân sách mới.** Request3 schemaFAIL giữ trong artifacts; request4 API/cachePASS. Các script paid probe refuse output-folder reuse; không rename/delete folder để vượt giới hạn. Exact billed spend chưa reconciled. Model outputs/raw synthetic aggregates không đưa vào Markdown report.

## Boot / validate

```powershell
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml config --quiet
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml up -d --build
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml ps
Invoke-RestMethod http://127.0.0.1:18001/health
Invoke-RestMethod http://127.0.0.1:18001/ready
```

Migrate one-shot service dùng explicit local QA URL và tạo riêng QA emulator bucket. Head0025_pipeline_checkpoints. Frontend `http://127.0.0.1:13000`, API18001, PG15432, S319000; dùng cùng127.0.0.1 để cookie site consistent. Production deployment phải TLS; QA smoke APP_ENV=test vẫn giữ auth/CSRF/RBAC. Production-mode TestClient proof không thay thế deployed TLS.

Sau sửa source, chỉ rebuild/recreate backend và worker khi jobs đã dừng/completed:

```powershell
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml build backend
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml up -d --no-deps backend worker
```

Không recreate Moto vì state volatile. Source-vs-image verification đã thực thi13module; `qa_final_snapshot.py` kết hợp build/activation/versions/hash nhưng refuse output reuse. Nếu cần execution mới, parameterize output mới trước; không xóa evidence cũ.

## Test commands

Mỗi output cần tên mới. Thay `<new-folder>` bằng folder riêng dưới audit artifacts; không paste placeholder angle brackets vào PowerShell.

```powershell
.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py --suite all --timeout 900
.venv/Scripts/python.exe -X utf8 scripts/qa_run_tests.py --output artifacts/full-system-20261009/new-targeted backend/tests/test_mdm/test_qa_regressions.py
.venv/Scripts/python.exe -X utf8 scripts/qa_live_system.py --mode smoke --output artifacts/full-system-20261009/new-smoke
node scripts/qa_browser.mjs --output artifacts/full-system-20261009/new-browser
```

Live smoke tự register/pin synthetic steward fixture **chỉ trong local QA DB**. Browser script cần fixture steward từ smoke; Edge path hiện `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`. Nếu máy khác, chỉnh executable path theo installed browser. Browser real/API không mock; existing `scripts/run_merge_frontend_smoke.mjs` là bộ API-mocked E2E riêng. Không gộp hai loại thành cloud E2E.

Viewer fixture: `qa_permissions.py` tạo own synthetic run dưới STEWARD rồi chuyển riêng fixture user về VIEWER; `qa_viewer_browser.mjs` kiểm disabled controls và read. `qa_browser_db.py --browser <browser-output> --output <new-db-output>` kiểm source/golden/export/split, stale revision, golden edit invalidates preview; temporary fixture override được clear. Những scripts chưa có output option hiện refuse/overwrite output cố định: phải parameterize trước rerun để giữ lịch sử.

Decode/benchmark chỉ10k:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/qa_dataset_adapter.py --root '../GenVN/benchmarks-v1' --output artifacts/full-system-20261009/new-datasets
.venv/Scripts/python.exe -X utf8 scripts/qa_live_system.py --mode datasets --datasets artifacts/full-system-20261009/datasets-v2 --only TC1_easy_match --output artifacts/full-system-20261009/new-live-tc1
.venv/Scripts/python.exe -X utf8 scripts/qa_benchmark.py --datasets artifacts/full-system-20261009/datasets-v2 --output artifacts/full-system-20261009/new-stage-benchmark
```

Adapter chỉ decode/concatenate, không sửa source/name/IDs hoặc dùng truth. Benchmark đọc CERTAIN labels **sau** computation. TC5 native422 là expected observed limitation, không gọi PASS hoặc rename/drop rows. `qa_candidate_recall.py` là probe opt-in, không release default.

`qa_operations.py` có actual local outage/kill tests; `qa_database_transactions.py` tạo/drop own local fixture và bounded read-only Neon calls; `qa_real_storage.py` real R2 owned-prefix multipart probes; `qa_cloud_validation.py --output <new-folder>` thực sự dùng staging100records và R2. Đọc scripts trước rerun, kiểm project/namespace và giữ LLM OFF. Không chạy chaos trên developer/prod DB.

## Experimental flags rehearsal

Chỉ local synthetic QA, không production. Khi không có job chạy:

```powershell
$env:QA_ENGINE_V2='true'
$env:QA_RESUME='true'
$env:QA_STREAMING='true'
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml up -d --no-deps backend worker
.venv/Scripts/python.exe -X utf8 scripts/qa_flags.py --output artifacts/full-system-20261009/new-v2
```

Sau test completed, restore cùng shell:

```powershell
$env:QA_ENGINE_V2='false'
$env:QA_RESUME='false'
$env:QA_STREAMING='false'
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml up -d --no-deps backend worker
```

Existing selections remain pinned; đổi flags không rewrite historical/human data. V210k completed nhưng chậm~474,5s; không bật làm default vì vừa có code.

## Debug / recovery / safe stop

Liveness200 mà readiness503: kiểm đúng QA DB/schema/storage. Logs worker có run_id/error_type/safe frames; không dump SQL params/secrets. Task retry/claim ownership ở PG, không clear/reset queue thủ công. Khi worker crashed, supervisor/lease recovery phải tạo attempt mới; verify run counts và unique published pairs. Missing export409: điều tra DB-owned object reference và actual object; không mark complete giả hoặc ký URL tới object mất.

Giữ volumes/worktree/artifacts/ignored env khi pause. Có thể `stop backend worker frontend database` ở đúng QA project; **giữ storage running hoặc fresh backup toàn dedicated emulator bucket trước khi stop**. `stop` cũng mất in-memory Moto state, không chỉ `down`. Không dùng `down -v`, broad `docker prune` hoặc cleanup computed paths ngoài workspace. Cleanup real R2 chỉ explicit keys/prefix của probe tự tạo.

Để tiếp tục công việc còn mở: đọc [PRODUCTION_READINESS](PRODUCTION_READINESS.md) và latest machine `audit-state.json`, inspect git diff/health, không restart toàn audit hoặc gọi lại canaries. Nếu cần credentials mới, yêu cầu user tự điền ignored env; không gửi secret qua chat.
