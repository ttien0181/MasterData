# Project map: P-119 / OneMaster

Ngày kiểm tra: 2026-10-08. Đọc cùng [rules](AGENT_RULES.md), [contracts](CONTRACTS.md), [baseline](BASELINE.md), [status](STATUS.md).

## Repository thực tế và snapshot

| Repository | Đường dẫn đã xác minh bằng filesystem và Git | HEAD trước task | Quyền |
| --- | --- | --- | --- |
| P-119 | `C:\VinUni\Dự án nhóm\P-119` | `937bfa9dc314cb4e64ee590ee05aae6aa5e618e4` (`version1`) | Chỉ nơi này được sửa |
| OneMaster | `C:\VinUni\Dự án nhóm\onemaster-workspace` | `bc5fb6ea10d0c0771a96efff3fe9a5d806f0b1f5` (`NTN`) | Chỉ đọc working tree |

P-119 không có tracked/staged diff khi bắt đầu. Có nhiều untracked: `.npm-cache/`, `benchmark/`, `data/*improvement_progress.py`, `eval/results/*`, `gemini_3_5_flash_lite/`; tất cả được giữ nguyên. OneMaster có nhiều modified và untracked, kể cả coordinator streaming, artifact, generation staging và tests. Do đó **HEAD OneMaster không đại diện đầy đủ source đã đọc**; phải đóng băng/hash working-tree module được chọn trước task chuyển giao. Không commit hay sửa repository tham chiếu.

SHA-256 working-tree cho các điểm tham chiếu chính (2026-10-08; không phải snapshot toàn repository):

| OneMaster file | SHA-256 |
| --- | --- |
| `src/onemaster/schema.py` | `5A1259211B8B37A7C0F427401A7DE138624C0843508845A3F322F8E583442CB4` |
| `src/onemaster/io/artifact.py` | `EC23DADBDBA8D764F3E75C891D070FB7C0F9698FC218B0A75E082B6E73DA2740` |
| `backend/app/worker/runner.py` | `50EC67E64DB7FF2D9CD5B514E0711C62C9382360F0A88645BFC7E0A1F9F6B6B6` |
| `backend/app/worker/background_upload.py` | `C83504C38AE315457AED9EF9E3DD9E080D55A8058520467AF5FBCE9E9794D9E8` |
| `backend/app/worker/prepare_normalize_stream.py` | `448754B1328B8D66954D9E83AFB2244E5F9C40EEEE9EBDB2B777F7DFBC9663F0` |
| `backend/app/worker/build_score_stream.py` | `95692558C27300C241A1D3C5293B2758D30AF0984D0D6EF0902BC3A6CDC268A4` |
| `backend/app/worker/score_decide_prep.py` | `BE4A4191B89CAA90A9CF48CCB6A67C98F5FC3FBA720A6AB4E970AB1158C15AFB` |
| `backend/app/worker/decide_materialize_stream.py` | `59B97D87381CCDFC968E09EA08E13A6FA2C8EF8CBCF27470C0A877B22D4D7BEE` |
| `backend/app/worker/generation_staging.py` | `13C3FF1A320BEB91A143688A65DA861AE42CE29DA30D0A7300B1E865FFFC1245` |

## Cấu trúc toàn repository P-119

| Vùng | Trách nhiệm và tình trạng |
| --- | --- |
| `backend/src/masterone/main.py`, `config.py` | FastAPI, router mounting, lifespan, PostgreSQL profile; execution worker độc lập (xem WORKERS.md) |
| `backend/src/masterone/api/` | Auth, runs, draft/chunk uploads, resolution API; có `large_imports.py` nhưng **không được main mount** |
| `backend/src/masterone/mdm/` | Import/upload sessions, resolution service/worker, review/items/pairs, preview/export/split, analytics, models và policy |
| `backend/src/masterone/mdm/pipeline/` | Standardization, blocking, TF-IDF, rule matching, constraints/floor/policy, semantic address, clustering/guards, golden, StageRunner |
| `backend/src/masterone/storage/` | R2, object store, manifest, atomic Parquet, checkpoint, stage registry, jobs/job_stages repository; additive artifact.py/background_upload.py cho attempt-isolated partitions (PARTITION_ARTIFACTS.md); chưa thay stage coordinator |
| `backend/src/masterone/pipeline/` | Generic chunk workers và CSV merge; không đồng nhất với resolution engine đang phục vụ UI |
| `backend/src/masterone/auth/`, `db/` | User/session, capability/CSRF/ownership; engine/session factories |
| `backend/src/masterone/llm/`, `agents/`, `services/` | LLM gate/cache/retry/audit/semantic/explanation; LangGraph demo chat cũng được mount |
| `backend/migrations/` | Alembic; lịch sử nhiều branch được merge; current head `0022_generation_staging` (baseline `0020_phase5_entities_and_edges`) |
| `backend/tests/` | MDM/API/auth/RBAC/upload/workflow/pipeline/migration/storage/LLM/agent tests; root `pytest.ini` chọn vùng này |
| `frontend/app/` | Next routes `/`, `/app`, `/login`, `/register`, layouts/CSS; **không phải frontend/src/app** |
| `frontend/src/components/` | Resolution workspace, intake/upload, pair review, analytics/audit, split/golden/policy UI; tests colocated |
| `frontend/src/lib/` | `contracts.ts`, `api.ts`, choices/localStorage, session/permission helpers; public client contract |
| `frontend/e2e/` | Playwright; cần Edge và service riêng. Không chạy trên service developer đang có |
| `scripts/` | Diagnostics, evaluation, HTTP/storage scripts. `capture_mdm_baseline.py` dùng module legacy không còn trong current source; không phải baseline của luồng hiện tại |
| `docs/`, `presentation/`, `reports_daily/` | Thiết kế, hướng dẫn, báo cáo, mockups; dùng làm bối cảnh, không thay thế code |
| `data/`, `masterone_test/`, `eval/`, `benchmark/`, `test_algorithm/` | Fixture/dataset, kết quả nghiên cứu/benchmark; không suy ra đang được main gọi; không chạy script tùy ý vì có thể gọi dịch vụ thật |
| `guide-neon-sqlalchemy/`, `gemini_3_5_flash_lite/` | Ví dụ/tài nguyên riêng, ngoài migration này |
| `src/`, `tests/`, `config/` ở root | Không có file được `rg --files` trả về trong lượt inventory này; entrypoint thật nằm ở backend |
| `.github/workflows/ci.yml`, Docker/Makefile/requirements | CI Python 3.11, frontend Node 22; dependencies backend trong `backend/pyproject.toml`, root requirements editable |
| `venv/`, `.venv/`, `node_modules/`, caches, artifacts | Môi trường/generated; không commit. `.venv` được tạo riêng cho baseline này, không sửa venv cũ |

Inventory bằng `rg --files` và `git ls-files`; đọc implementation của các vùng runtime, schema và test liên quan. Không tuyên bố đã đọc từng dòng mọi dataset, tài liệu, binary hoặc module nghiên cứu.

## Luồng chạy thật của P-119

1. `uvicorn masterone.main:app`: lifespan lấy `active_mdm_database_url`, gọi `require_current()` (có thể auto-upgrade DB!), tạo session factory; khởi động `ResolutionWorker` trừ `APP_ENV=test`.
2. Frontend gọi trực tiếp API base + `/api/v1/mdm`, dùng cookie credentials và CSRF; không có proxy Next bắt buộc trong đường này.
3. Intake: `mdm_uploads` -> `upload_sessions` -> validate CSV/mapping -> commit -> `resolution_import`; hoặc multipart `mdm_runs.create_run` -> `create_resolution_run`. Import ghi record theo batch, chuẩn hóa ngay; có raw bytes R2 tùy cấu hình.
4. `POST /runs/{run_id}/start` -> `resolution_service.create_task`, task QUEUED trong DB; API không dispatch generic StorageJob.
5. `resolution_worker._work` poll task; `resolution_service.run_pipeline` đánh dấu RUNNING và chọn nhánh theo record count.
6. **<=10.000:** ORM snapshot -> `_compute` -> CandidateEngine -> full-snapshot ScoringEngine -> semantic enrichment -> `build_proposals`/review grouping -> preservation transaction; opt-in generation path thêm staging/durable receipt/validation trước promote.
7. **>10.000:** Service dùng `StageRunner.run_all(records_raw=None, return_data=True)`, rồi publisher chung; direct `return_data=False` cũng qua gate khi generation enabled/protected run. PostgreSQL qua DuckDB extension; PREPARE -> NORMALIZE -> BUILD_CANDIDATES -> SCORE -> DECIDE -> CLUSTER -> MATERIALIZE tuần tự. File Parquet không đồng nghĩa toàn engine bounded-memory.
8. API items/overview/analytics lấy serving tables. Review UI dùng `resolution_pairs.decide_review_pair` và `resolution_items.transition_review`; không mặc định dùng legacy `resolution_review.approve_case`.
9. Golden override -> proposal; preview freezes policy/pair fingerprint/proposal revisions/choices -> execute export + merge events -> split sinh export hậu duệ; audit gắn actor.

### Những khác biệt đã xác nhận, chưa sửa

- Tại baseline, large BUILD chỉ exact name + TF-IDF. Task CandidateEngine đã thống nhất contacts/ID/grams/composites/TF-IDF cho hai nhánh; xem [CANDIDATES](CANDIDATES.md). Ngưỡng 10.000 vẫn chọn orchestration/persistence.
- SCORE đã dùng ScoringEngine chung, full normalized snapshot census và typed batched Parquet; xem [SCORING](SCORING.md). Rule scorer mặc định; Splink adapter thật vẫn bị chặn nếu thiếu frozen trained/calibrated/evaluated model phù hợp.
- NORMALIZE "pass 2" hiện COPY, không có aggregate như comment; exception từng row bị bỏ qua.
- TF-IDF mặc định giữ partition DOB/province và argpartition legacy, nhưng không còn dense similarity block `.toarray()`. Opt-in sparse global Top-K có full-record IDF/workload guard: xem [SPARSE_TFIDF](SPARSE_TFIDF.md). SCORE không giữ full scored_pairs list; DECIDE/CLUSTER decisions và records vẫn dùng RAM.
- StageResult list và ORM model tồn tại nhưng StageRunner không persist/resume chúng; context config hash rỗng, artifact path không namespace attempt.
- Worker chưa claim ownership nguyên tử; startup reset tất cả RUNNING; rủi ro nhiều API process.
- Decision task đã thêm evaluator/adapter và retry preservation chung. `REJECTED` là execution veto riêng; cannot-link lấy từ active negative edges/historical explicit `NON_MATCH`. Corrected person semantics opt-in: xem [DECISIONS](DECISIONS.md).
- Small/large/direct serving persistence có opt-in generation/private staging/durable receipt/publication gate chung. Five additive tables ở migration 0022, flag mặc định OFF; xem [GENERATIONS](GENERATIONS.md). PostgreSQL throughput/concurrency chưa kiểm định.

## Mapping sang OneMaster (source, không chỉ docs)

| Concern | P-119 cần xem/sửa ở task sau | Module tham chiếu OneMaster |
| --- | --- | --- |
| API/service boundary | `api/mdm_runs.py`, `api/mdm_resolution.py`, `mdm/schemas.py` | `backend/app/main.py`, `api/routes.py`, `services/resolution.py`, `schemas/` |
| Source contract/PREPARE | `resolution_import.py`, `upload_sessions.py` | `src/onemaster/schema.py`, `io/preparation.py`, `configs/phase1.yaml` |
| Normalization/census | `pipeline/standardization.py`, `stage_runner.py` | `normalization/pipeline.py`, field normalizers, versioned references |
| Candidates/budget | `blocking.py`, `tfidf_blocking.py`, `stage_runner.py` | `candidates/pipeline.py`, `planner.py`, `exact.py`, `composite.py`, `tfidf.py`, `union.py`, `workspace.py`; Lucene optional |
| Score | `matching.py`, large SCORE | `matching/candidate_scoring.py`, `splink_adapter.py`; model identity/calibration |
| Evidence/policy | `matching.py`, `hard_constraints.py`, `evidence_floor.py`, `policy_engine.py` | `decision/evidence.py`, `constraints.py`, `policy.py`, `general.py`, `evaluator_v2.py`, `contracts_v2.py` |
| Cluster/survivorship | `clustering.py`, `cluster_guards.py`, `golden.py` | `clustering/constrained_union_find.py`, `person_guard.py`, `golden/person_survivorship.py` |
| Orchestration/recovery | `resolution_worker.py`, `resolution_service.py`, `stage_runner.py` | `backend/app/worker/runner.py`, `repository.py`, `tasks.py`, `resources.py`, `core.py` |
| Immutable partitions | `storage/parquet_io.py`, `manifest.py`, `checkpoint.py` | `src/onemaster/io/artifact.py` DatasetWriter/manifest/leases |
| Bounded fan-out | Chưa có coordinator resolution tương đương | `background_upload.py`, `prepare_normalize_stream.py`, `build_score_stream.py`, `score_decide_prep.py`, `decide_materialize_stream.py` |
| R2/retry/ack | `storage/r2.py`, `object_store.py` | `backend/app/adapters/r2.py`, uploader, committed manifest |
| Publication | `stage_runner.py` MATERIALIZE, ORM persistence, models/migrations | `materialize.py`, `materialization_generation.py`, `generation_staging.py`, partition receipts |
| HITL/replay | `resolution_items/pairs/preview/split/review.py` | `services/hitl.py`, `person_cases.py`, `person_resolution.py`, guarded merge worker; **giữ nghiệp vụ P-119** |

OneMaster API external worker/RQ khác P-119 API-thread worker. Partition mới đã có trong working tree; flag `enable_partition_stream_*` mặc định tắt. Legacy artifact overlap/PREPARE partition là chế độ khác, không được gộp báo cáo.

Streaming references: partition được seal local rồi fan-out uploader và consumer bằng metadata/file reference. PREPARE pass1 overlap nhưng NORMALIZE final có census barrier; BUILD global candidate union rồi mới emit final ranges; SCORE pair preprocessing có thể overlap nhưng DECIDE global sorted union phải chờ; DECIDE materialization bắt đầu từ **kết quả nghiệp vụ final**, staging không công bố sớm. Global TF-IDF/record/union state vẫn tồn tại. General policy có thể bỏ preprocessing và tính lại evidence.

## Test map và bước sửa dự kiến

| Nhóm | Tests hiện có / mới |
| --- | --- |
| Intake/schema | `test_resolution_import_schemas.py`, `test_upload_sessions.py`, `test_document_pipeline.py` |
| Public UI contracts | `test_session_contract.py`, `test_sessions_hub.py`, `test_resolution_items.py`, **`test_merge_baseline_contracts.py`** |
| Algorithms | `test_standardization.py`, `test_tfidf_blocking.py`, `test_hard_constraints_and_floor.py`, `test_cluster_guards.py`, `test_policy_*`, `test_regression_*`, `test_stage_runner.py` |
| Review/export/audit | `test_vertical_slice.py`, `test_resolution_items.py`, `test_document_pipeline.py`, `test_remaining_tasks.py`, `test_audit.py`, `test_rbac.py` |
| DB/storage | `test_migrations.py`, `test_storage_registry.py`, `test_mdm_stage_registry.py`, `test_object_store.py`, `test_pipeline_workers.py` |
| Frontend | `frontend/src/**/*.test.ts[x]` Vitest, `frontend/e2e/` Playwright; không sửa frontend |
| OneMaster invariants | `test_prepare_normalize_stream`, `test_build_score_partition_stream`, `test_score_decide_prep`, `test_decide_materialize_partition`, `test_background_upload`, generation/recovery tests; chỉ đọc, không chạy trong task này |

Thứ tự dự kiến: characterization/equivalence -> approved versioned input contract -> artifact/checkpoint protocol -> worker ownership -> bounded fan-out -> algorithm unification -> staged publication -> shadow/canary. Đây là backlog, không phải các thay đổi đã triển khai.


## Engine/API selection adapter

`mdm/engine_adapter.py`: EngineResultAdapter, pinned run selection/context và
text/plain JSON transport compatibility. `resolution_persistence.py` là
projection boundary chung cho legacy/staged/direct runner. Resolution router
dùng pinned profile cho review/golden; list/detail giữ frontend DTO. Private
migration 0023 giữ profile qua rollback. Tests: `test_engine_adapter.py`; actual
frontend consumer smoke: `scripts/smoke_frontend_api.mjs` (Node 24), không sửa UI.
Xem [ENGINE_ADAPTER](ENGINE_ADAPTER.md).

## Controlled streaming

`mdm/pipeline/streaming.py` orchestrates immutable reference exchanges and global
barriers, called by `_compute`/StageRunner in the independent worker. Native
ArtifactWriter + BackgroundUploader overlap with downstream consumers.
`generation_staging.py` optionally stages native references before the existing
seal/promote gate. Tests: `test_streaming.py`, suite `--suite streaming`.
Flags, capacities, fallback and limitations: [STREAMING](STREAMING.md).

## Compute checkpoint recovery

`mdm/pipeline/checkpoints.py`: versioned manifests, dependency validation, bounded
DB object adapter, owned atomic commits, stage/partition reuse and explicit GC.
`streaming.py` connects upload acknowledgments and global barriers; `_compute`
and DB StageRunner opt in. `generation_staging.py` validates SEALED generation
recovery before promotion. Migration 0025 adds private checkpoint tables.
Tests: `test_checkpoint_resume.py`; `--suite resume`. [CHECKPOINTS](CHECKPOINTS.md)
documents identity, storage, retention and rollout limitations.
