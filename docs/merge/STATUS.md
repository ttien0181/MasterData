# Migration status

Task mới nhất (2026-10-09): toàn bộ migration validation/shadow trước production;
xem [FINAL_REPORT](FINAL_REPORT.md), [BENCHMARK](BENCHMARK.md),
[SHADOW_DIFF](SHADOW_DIFF.md), [ROLLBACK](ROLLBACK.md).

## Completed — Final migration validation

- Đọc toàn bộ docs/merge, lịch sử và migration changes; dùng commit `937bfa9`
  làm old engine thật, không nhầm `p119_current` tại HEAD với code trước migration.
- Pin current runtime `513cbae`, source/config/input hashes và dirty OneMaster
  reference; chạy OneMaster từ bản sao trong P-119, không sửa workspace tham chiếu.
- Labeled shadow với 4.213 team-generated PERSON records/1.200 pair labels và
  864 public Fodors/Zagats restaurant records/112 gold links. Public set ngoài
  miền PERSON; không gọi nó là enterprise PERSON acceptance. Labels không vào
  matching, unlabeled pairs không tự thành negatives; rule scores không giả xác suất.
- 14 completed legal compute cases; 8 serial/batch/partition/worker comparisons
  PASS khi tách đúng scoring config pin. Full hashes khớp với fixed batch khi
  đổi workers/partitions. Actual reverse completion được chứng minh trên snapshot
  4.213 records ở NORMALIZE/SCORE/DECIDE. Ba probes timeout 300s; tiny batch bị
  dừng chưa hoàn tất; exploratory p4096 ngoài schema bị loại khỏi acceptance.
- Old/current/v2 có cùng candidate sets, scores, decisions, memberships/review
  trên hai snapshots. Evidence/provenance/model config metadata changes được
  giải thích; 8 internal golden payloads đổi metadata, selected values không đổi.
- Đo local runtime/RSS/CPU/disk/queue/waits/upload, SQLite stage/promote/replay
  và CSV/Parquet export. Không kết luận v2 nhanh hơn/tốt hơn. Streaming vẫn giữ
  global matching state; không chứng nhận O(batch) memory của whole worker.
- Rehearsal SQLite riêng batches 128/512: không serving trước promote, đầy đủ
  counts sau publish/replay, pair values/evidence readback, full export coverage.
- Phát hiện binary rollback incompatibility: reader `937bfa9` đọc export mới
  `local:`/NULL csv_bytes trả HTTP 500. Runbook ưu tiên giữ compatible runtime,
  rollback selection cho run mới, giữ pinned profiles/generation/ownership/history.
- Readiness kết luận FAIL. Không bật production flag, không live migration/R2/
  LLM/deployment; production approval và acceptance criteria chưa được cung cấp.

## Modified files — Final migration validation

- `.gitignore`: bỏ qua generated validation evidence.
- New `scripts/merge_shadow_validation.py`, `scripts/run_onemaster_validation.py`,
  `scripts/run_merge_frontend_smoke.mjs`.
- New `backend/tests/test_mdm/test_shadow_validation.py`.
- New docs/merge FINAL_REPORT.md, BENCHMARK.md, SHADOW_DIFF.md, ROLLBACK.md;
  STATUS.md updated. Runtime implementation/frontend source/lockfile không đổi.

## Tests — Final migration validation

| Executed check | Outcome | Evidence |
| --- | --- | --- |
| `run_merge_baseline.py --suite all --timeout 900` | FAIL: 840 passed, 11 failed, 1 skipped; 390.11s | merge-baseline/20261009T004216Z-pb91gsim |
| Failure-ID comparison to pre-export baseline | PASS: same 11 IDs; no new backend failure IDs | compared XML with 20261009T002427Z-uoxjv49f |
| New harness correctness tests | PASS: 6 tests | merge-validation/harness-tests-final |
| Labeled matching shadows | PASS measured completed equivalence; completion probes FAIL/NOT_TESTED as reported | merge-validation/20261009-shadow-v2 |
| Native OneMaster 8 selected offline suites | FAIL: 120 passed, 6 failed, 108 warnings; 395.25s | merge-validation/om4 |
| SQLite migration/publication/replay/export on both snapshots, batches 128/512 | PASS | db-public/internal-b128/b512 metrics.json |
| Legacy export-reader rollback probe | FAIL: download/sample HTTP 500 | rollback-reader.json |
| Frontend `npm ci`, TypeScript, Vitest, Next build | PASS: 50 tests / compiled routes | local tools; lockfile unchanged |
| Browser dev + compiled Next CLI E2E | PASS: 10 + 10 tests | frontend-1791507136688, frontend-1791507871165 |
| Actual api.ts/backend consumer smoke; kill/retry/cancel/resume; mock R2 faults; overrides/coverage; migration preservation | PASS within local fixtures | full backend baseline |
| Ruff new Python modules/tests; Node syntax; `git diff --check` | PASS | local tools |

Evidence folders are under ignored `artifacts/`; no raw datasets/PII/logs staged.
Preliminary OneMaster runs exposed runner socketpair/reference-data/Windows-path
setup errors; fixed isolation/copy setup without modifying reference code or
assertions. Final native failures include empty-model bootstrap and score/model/
review parity; không đổi chúng thành expected PASS.

## Pending / Dependencies — Final migration validation

- 11 backend baseline failures và 6 native OneMaster failures chưa được giải
  quyết/waive; no production approval. Incomplete small-partition probes và
  global memory/index scaling cần profiling và budget validation.
- Chưa có independent enterprise PERSON holdout/complete entity truth hoặc
  calibrated approved model; zero observed FPs trên ít nhãn không chứng minh
  safety. Candidate recall sample 98.33%; auto recall 0.2083%; không cải thiện
  old/current/v2 trên snapshots này.
- PostgreSQL locking/heartbeat/publication/restore, real R2/network faults/upload,
  live full-stack browser/API/worker, standalone container, representative scale/
  concurrent load và p95 capacity: NOT_TESTED. SQLite/mock/browser specs không
  thay thế các rehearsal này.
- Binary downgrade cần giữ/backport export reader và ownership/publication gates;
  không rerun protected runs bằng writer cũ, không xóa pinned/history state.

## Commit / Next task — Final migration validation

Parent `513cbae`; commit message
`test(merge): validate migration shadows and document production blockers`.
Hash báo sau commit. Tiếp theo: owner chốt acceptance/approval, xử lý failures,
cấp isolated PostgreSQL/R2 + enterprise truth, chạy lại fixed-model/full-stack/
rollback/load rehearsals. Không tự thực hiện production rollout.

Task trước (2026-10-09): streaming export và resource telemetry;
xem [EXPORTS](EXPORTS.md).

## Completed — Streaming export

- Đọc docs/merge và luồng export/preview/R2; xác minh hai repository, chỉ sửa
  P-119, giữ WIP/untracked. Không delegate hoặc gọi live services.
- Thay full records/by_id/rows/StringIO/bytes bằng keyset batches, CSV tạm và
  SQLite disk indexes cho ownership/review connected components/exact-once coverage.
  Optional Parquet companion ghi batches, đủ 10 cột theo schema strings cố định.
- Giữ BOM/CRLF/quoting/field order/row order/source identity/review_pending,
  selected golden và overrides; không đổi frontend hoặc DB schema.
- Validate missing/foreign/overlap sources, full coverage/row equation, CSV header,
  Parquet footer/schema/rows; SHA-256/size và bounded remote readback.
  Upload file đóng hoàn chỉnh với một R2 transfer worker, complete manifest trước
  transaction publication. Upload interruption fail closed, không bị nuốt exception.
- Preview retry giữ export ID ổn định, run-lock và immutable historical snapshot.
  Direct export hỗ trợ optional Idempotency-Key header có actor/run namespace và body
  conflict validation. Giữ attachment/X-Export-Id/compare/download/R2 redirect.
  Không R2 dùng durable local artifact reference, csv_bytes=NULL cho export mới.
- Pair fingerprint và merge events stream pairs; ownership/edge indexes spill
  SQLite thay vì giữ toàn bộ pair IDs/records. Preview revision/member metadata
  vẫn theo số proposals; một group's source data bounded bằng group budget.
- Resource metrics trong audit/manifest/log: peak process RSS, temporary disk/spill
  peak, queue depth/backpressure wait (serial: 0), upload+readback/stage durations.
  Configurable batch/group/disk/RSS/time budgets, explicit HTTP 507/504 failures.

## Modified files — Streaming export

- `.env.example`, `backend/src/masterone/config.py`
- `backend/src/masterone/mdm/export_stream.py` (mới)
- `backend/src/masterone/mdm/resolution_items.py`, `resolution_preview.py`
- `backend/src/masterone/api/mdm_resolution.py`
- `backend/src/masterone/storage/object_store.py`
- `backend/tests/test_mdm/test_export_stream.py` (mới), `test_upload_sessions.py`
- `scripts/run_merge_baseline.py`
- `docs/merge/EXPORTS.md` (mới), `CONTRACTS.md`, `STATUS.md`

## Tests — Streaming export

`.venv`, Python 3.11.9/Windows, source PYTHONPATH; isolated SQLite/R2/LLM/tracing
OFF và artifact roots riêng. Không app lifespan/DB doanh nghiệp/R2 thật/frontend.
Runner: `.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py`.

| Command/check | Kết quả thực thi | Report dưới artifacts/merge-baseline |
| --- | --- | --- |
| `--suite all`, baseline trước thay đổi | 818 passed, 11 failed, 1 skipped; 369.46s; exit 1 | `20261009T002427Z-uoxjv49f/` |
| `--suite exports`, regression + 22 tests mới | 82 passed; 37.60s; exit 0 | `20261009T003117Z-lr819tuc/` |
| `--suite exports`, thêm Unicode/quoting/multiline/large field compatibility | 83 passed; 39.43s; exit 0 | `20261009T003352Z-ql2t2fzk/` |
| `--suite all`, trước DTO header fix | 837 passed, 13 failed, 1 skipped; 364.74s; exit 1 | `20261009T003209Z-_bxlcvjr/` |
| `--suite exports`, final gồm API contracts + pipeline streaming | 133 passed; 148.47s; exit 0 | `20261009T003857Z-o4o4nr4l/` |
| Ruff: export_stream/resolution_items/resolution_preview/object_store/test_export_stream/config/test_upload_sessions/runner | PASS | Tool output |
| Ruff: api/mdm_resolution.py | Existing F841, benchmark route unused run (có trong parent); không tuyên bố whole-file PASS | Tool output |
| `git diff --check` | PASS | Tool output |

Fresh subprocess memory probes (128 → 4096 rows, mỗi raw name 4 KiB), JUnit
properties trong export report lưu số đo thật:

| Batch rows | Python peak bytes, 128 → 4096 | OS peak RSS growth bytes, 128 → 4096 |
| --- | --- | --- |
| 16 | 4504455 → 5197996 | 10223616 → 10518528 |
| 128 | 4540465 → 5234938 | 10756096 → 11182080 |

Python peak tăng ~0.7 MiB khi CSV tăng ~16 MiB; đây là local regression, không
chứng minh production throughput/RSS hoặc triệu rows. Tests mới gồm multi batches,
CSV/Parquet empty/schema/coverage, selected source/override priority, upload
interruption/corrupt readback/manifest failure, completed retry/conflicting body,
preview replay/download, resource budget failures và large Unicode raw field.

Exploratory: adapter `20261009T002632Z-wkrhdvhz/` 183 passed/1 failed (legacy R2
compare mock binding); exports `20261009T002845Z-ssa01teu/` 78 passed/2 failed
(disk fixture chưa đạt cap, Parquet handle chưa đóng trên Windows);
`20261009T003008Z-wr9q991o/` 79 passed/1 failed (test Arrow path vượt Win32 MAX_PATH).
Đã sửa và kiểm thử lại; không sửa/suppress failures baseline ngoài scope.

Lượt full có 11 failures baseline, một DTO regression do optional body field
và một WinError 5 khi pipeline streaming replace `_manifest.json`. DTO đã sửa
bằng optional `Idempotency-Key` header, giữ nguyên ExportRequest body mặc định;
không sửa assertion cũ. WinError 5 nằm ở code manifest ngoài export; nhóm tests
streaming/API contracts được chạy lại cùng export: 133 passed, WinError không tái
hiện và DTO contract cũ PASS. Lượt full chạy trước header fix; không rerun full
sau đó, không tuyên bố whole-backend suite PASS.

## Pending — Streaming export

- Isolated PostgreSQL concurrent export/review/golden locks và real R2 multipart
  interruption/readback/throughput chưa kiểm định; không gọi live services.
- Local artifact directory cần durable/shared volume cho nhiều API processes.
  Export orphan content GC sau upload/DB failure là task riêng; artifacts không
  có committed export không được expose. Không dùng checkpoint GC xóa lịch sử export.
- Budgets cooperative theo batch/chunk/callback, không phải OS hard caps. One
  field/group có thể transiently exceed cap; preview metadata và một merge event
  vẫn theo group/proposal size. Không claim whole API O(1) memory.
- 11 failures/1 skip baseline (10 StageRunner fixtures thiếu source/external identity,
  1 APP_ENV=test CORS expectation; existing TF-IDF skip) vẫn ngoài scope.

## Dependencies — Streaming export

- Không thêm dependency; SQLAlchemy/boto3/PyArrow có sẵn, SQLite/ctypes/resource
  từ standard library. Không migration DB hoặc runtime import OneMaster.
- Config/defaults/schema/local storage lifecycle xem EXPORTS.md và .env.example.
- Legacy direct call không request ID tiếp tục tạo snapshot mới; retry idempotent
  dùng preview execute_request_id hoặc optional direct Idempotency-Key header.

## Commit — Streaming export

Parent `f810d9e2797bd4a9fd3eea1b1ec8b2c38115b4b6`.
Message `feat(mdm): stream validated export artifacts with resource telemetry`.
Hash báo final; chỉ stage task files, không artifacts/WIP/user untracked.

## Next task — Streaming export

Rehearsal isolated PostgreSQL/R2 concurrency/fault behavior và provision durable
artifact storage trước rollout; export-specific orphan GC khi được giao.

---

# Lịch sử: Safe retry/resume

Task mới nhất (2026-10-09): safe retry/resume;
xem [CHECKPOINTS](CHECKPOINTS.md).

## Completed — Safe retry/resume

- Đọc docs/merge và code worker/artifact/streaming của các phiên 11–13,
  legacy checkpoint, generation gate, scoring validation và reviewer persistence.
  Chỉ sửa P-119; OneMaster read-only, không delegate hoặc thêm broker.
- Strict versioned checkpoint manifest pin snapshot/payload, config, model,
  reference/stage version, input/output artifact hash, run/attempt và dependency
  manifest hashes. DB commit index authoritative; LOCAL_COMPLETE tách DURABLE_COMPLETE.
- Owned run-lock transactions trước/sau commit, upload callback giữ captured claim;
  duplicate ack idempotent, conflict fail closed, không downgrade durable state.
  Stage commit đợi toàn bộ partition receipts, complete remote manifest và dependencies.
- Reuse xác thực graph, upstream input hashes, committed native identity/coverage,
  remote SHA/size/receipt, Parquet footer/schema/rows. Partial/local/stale/corrupt
  checkpoint không được nhận; log safe RESUME/recompute/invalid reason và origin.
- Resume PREPARE/NORMALIZE partitions, full BUILD, individual SCORE partitions,
  pair-local DECIDE prep và full CLUSTER result; reconstruct fresh score artifact.
  Giữ global census/candidate union/coverage/guarded-union barriers, canonical order.
  Copy bytes đã verified sang current attempt manifest/key, không trộn identities.
- SEALED generation validate/promote trực tiếp; partial private materialization
  dùng idempotent receipts. Existing serving uniqueness/fence/persistence bảo vệ
  pairs/golden/review/membership, active pointer, reviewer decisions và golden overrides.
- Independent pipeline_resume_enabled default OFF, pinned profile; compute resume
  rule/no-semantic, serial fallback cho đường chưa hỗ trợ. Worker small/DB StageRunner
  được nối; API/frontend giữ nguyên. Không R2 dùng bounded immutable private DB objects;
  remote runtime failure không tự đổi backend hoặc nhận partial data.
- Conservative explicit GC: pin directory, kiểm tra lease/root/dependency/job/active
  generation, retire GC_PENDING trước khi xóa; tombstone và lock files được giữ,
  crash/deletion failure retry an toàn. Generic cleanup không xóa pinned directory.
- Migration 0025_pipeline_checkpoints sau 0024; private object/index/dependency tables.
  Downgrade giữ history, re-upgrade idempotent; không migrate DB thật trong phiên.

## Modified files — Safe retry/resume

- New `backend/src/masterone/mdm/pipeline/checkpoints.py`,
  `backend/migrations/versions/0025_pipeline_checkpoints.py`,
  `backend/tests/test_mdm/test_checkpoint_resume.py`.
- `backend/src/masterone/config.py`, `mdm/engine_adapter.py`, `mdm/models.py`,
  `mdm/migrate.py`, `mdm/generation_staging.py`, `mdm/resolution_service.py`,
  `mdm/pipeline/stage_runner.py`, `mdm/pipeline/streaming.py`.
- `backend/src/masterone/storage/artifact.py`, `background_upload.py`,
  `scripts/run_merge_baseline.py` (resume suite + explicit OFF isolation).
- docs/merge CHECKPOINTS.md (new), CONTRACTS.md, PARTITION_ARTIFACTS.md, PROJECT_MAP.md, STREAMING.md,
  WORKERS.md, STATUS.md. Không sửa .env/frontend hoặc untracked work ngoài task.

## Tests — Safe retry/resume

Python 3.11.9 `.venv`, Windows; isolated temporary SQLite/Parquet/LocalObjectStore,
R2/LLM/tracing OFF. Commands:
`.venv\Scripts\python.exe -X utf8 scripts/run_merge_baseline.py --suite all`
và `--suite resume`. Reports dưới `artifacts/merge-baseline`.

| Run | Result | Report |
| --- | --- | --- |
| all, pre-change baseline | 782 passed, 11 failed, 1 skipped; 205.58s | `20261008T165116Z-8ewaezfn` |
| resume, initial | 19 passed; 73.61s | `20261008T171255Z-zpaoktaj` |
| resume, kill/migration | 28 passed, 1 failed; 130.97s | `20261008T172044Z-uj8wmedf` |
| resume, partial SCORE/GC | 30 passed, 2 failed; 152.24s | `20261008T172650Z-y2ofsns3` |
| all, intermediate | 809 passed, 16 failed, 1 skipped; 360.41s | `20261008T172701Z-yiz6xvqy` |
| resume, intermediate while code pins changed | 30 passed, 2 failed; 147.76s | `20261008T173555Z-r62p55nd` |
| resume, fixed/frozen | 36 passed; 165.99s; exit 0 | `20261008T173931Z-0s_t5xpu` |
| all, expanded cases | 818 passed, 11 failed, 1 skipped; 366.90s | `20261008T173750Z-h6q2clg9` |
| all, pre-final empty input pin correction | 818 passed, 11 failed, 1 skipped; 376.88s | `20261008T231633Z-r4nqgja0` |
| all, empty input barrier regression | 817 passed, 12 failed, 1 skipped; 371.73s | `20261008T231951Z-8jew9r41` |
| isolated empty/overlap barrier correction | 2 passed, 34 deselected; 6.34s | `empty-pin-e97j5m58` |
| all, final frozen code | 818 passed, 11 failed, 1 skipped; 364.83s | `20261008T232658Z-renvf5vw` |

Intermediate fixes: migration fixture cleared isolated MDM_DATABASE_URL override
and used ORM defaults; partial kill now synchronizes after DURABLE commit, not local
ack; generation object adapters without exists retain upload compatibility.
Changing source while a child fault test was running correctly invalidated its
code pins; frozen resume rerun passed. Final empty overlapping partitions now use
the completed upstream hash instead of a pre-bound default, with a contract assertion.
NORMALIZE's zero-input overlap path pins its prepared source before close/upload;
the isolated rerun caught and verified this missing pass-one callback case.
Không weaken assertion/test cũ.

36 new cases include actual spawned process kill at six compute stage boundaries
and materialization seal, durable partial SCORE recovery, faulted SQL INSERT/rollback,
mock R2 outage, corrupt manifests/Parquet, stale pins/config/mode, duplicate ack,
cancel/reviewer preservation, dependency/native lease GC and tombstone crash/retry,
all-boundary overlap and empty snapshots. All passed in the expanded full suite.
Final frozen whole-suite XML confirms all 36 checkpoint cases passed. Exact
failure/skip ID comparison matches the same 11 baseline failures (CORS
APP_ENV=test and legacy StageRunner fixtures missing external_id); TF-IDF skip
unchanged. Whole-backend suite is not PASS.

Ruff changed modules/new tests/migration/runner PASS; StageRunner retains exactly
25 pre-existing diagnostics, code/message multiset unchanged. Added code has no new
diagnostics. Final `git diff --check`/staged diff check PASS; relative documentation
links valid. OneMaster working entries remain 227 with no staged changes;
unrelated pre-existing P-119 untracked work was not staged or changed.

## Pending / Dependencies — Safe retry/resume

- Flags remain OFF. Live isolated R2/PostgreSQL concurrency/outage, POSIX leases/fsync,
  representative shadow/scale/resource rehearsal and capacity tuning before rollout.
  Local tests do not establish production throughput or cross-host service behavior.
- Transport/disk/DB objects are bounded; full normalized snapshot, candidate index
  and clustering still use worker memory. Splink/global semantic compute stays serial.
- Explicit GC is conservative; active generation blocks its run's collection. No
  automatic scheduler/orphan directory adoption/cleanup. Retained files count against
  quota; interrupted files without DB index need operator retention handling.
- Stop workers and apply 0025 explicitly before deploying; rollback preserves
  private history. No live DB migration or new default enablement is authorized here.
- Existing 11 backend failures/1 skip remain outside this task; untracked benchmark,
  eval/artifact/data work remains untouched. No direct OneMaster runtime imports.

## Commit / Next task — Safe retry/resume

Parent `aaa4896`; commit message
`feat(pipeline): add fenced durable checkpoint retry and resume`.
Hash reported after commit. Next task: separately authorized production shadow,
service/concurrency/retention rehearsal and rollout; do not automatically enable flags.

---

Task trước (2026-10-08): controlled streaming boundaries;
xem [STREAMING](STREAMING.md).

## Completed — Controlled streaming boundaries

- Đọc docs/merge, native partition/ArtifactWriter/uploader, worker ownership,
  StageRunner, score/decision/cluster và generation publisher; đối chiếu bốn
  OneMaster boundary modules read-only, SHA-256/provenance trong STREAMING.md.
- PREPARE phát closed references cho NORMALIZE pass one; retained imported
  normalization, raw normalization cho StageRunner thiếu normalized fields.
  Complete snapshot barrier trước global contact census và BUILD admission.
- BUILD giữ global candidate union/deduplicate trước mọi SCORE partition;
  rule scoring trên frozen full snapshot. Shared write_scores validation giữ
  coverage, model/config identity, source hashes và census checks của serial.
- SCORE phát private pair-local DECIDE prep; complete score coverage, consumer
  drain và verified remote manifests trước global DecisionStore seal/guarded
  union. Canonical partition/candidate order, không dùng completion order.
- DECIDE/MATERIALIZE chỉ sau global clustering; native partition references
  cho private generation loads overlap uploader. Mỗi kind có barrier riêng;
  tất cả native uploads/DB receipts xong mới seal. Existing promote revalidates
  durable results, input/reviewer fingerprints và job attempt ownership.
- Reference queues bound queued/active/reserved partitions và bytes, reserve
  trước producer, giữ file lease; bounded worker/upload concurrency. Consumer
  ContextVars copy pinned engine/ownership. Timeout/error fail attempt;
  supervisor vẫn chịu cancel/deadline/heartbeat/recovery, không thêm broker.
- Native materialization reuse compute workspace/disk quota. Replay dùng fresh
  private directory, giữ content-addressed identity/DB checkpoint idempotence;
  không mutate committed file. Empty stage có zero-row validated partition.
  Arrow nested list names giữ nguyên qua Parquet để pinned schema hash đúng.
- Năm boundary flags riêng, default OFF, engine-v2 không tự bật. Saved profiles
  pin opt-in; profile cũ thiếu streaming flags vẫn OFF. Eligibility fallback
  serial có reason cho no-store/Splink/semantic/off; runtime errors không fallback
  vào partial serving. MATERIALIZE opt-in dùng existing generation gate.
- Nối worker `_compute` và large DB StageRunner path, DB producer yield_per;
  API/frontend không đổi, không thêm schema migration/dependency/LLM call.

## Modified files — Controlled streaming

- New `mdm/pipeline/streaming.py`, `backend/tests/test_mdm/test_streaming.py`.
- `backend/src/masterone/config.py`, `mdm/engine_adapter.py`,
  `mdm/resolution_service.py`, `mdm/generation_staging.py`,
  `mdm/pipeline/stage_runner.py`, `mdm/pipeline/scoring_engine.py`.
- `backend/src/masterone/storage/artifact.py`, `background_upload.py`.
- `scripts/run_merge_baseline.py` (streaming suite + explicit OFF isolation).
- docs/merge STREAMING.md (new), CONTRACTS.md, PROJECT_MAP.md, STATUS.md.

## Tests — Controlled streaming

Python 3.11.9 `.venv`, Windows; temporary SQLite/Parquet/LocalObjectStore,
R2/LLM/tracing OFF; no live enterprise DB/services. Commands use
`.venv\Scripts\python.exe -X utf8 scripts/run_merge_baseline.py`.
Reports under `artifacts/merge-baseline`.

| Suite / iteration | Executed result | Report |
| --- | --- | --- |
| all, pre-task | 751 passed, 11 failed, 1 skipped; 100.15s | `20261008T161951Z-vk9liyk4` |
| streaming, initial | 150 passed, 14 failed; 67.77s | `20261008T163215Z-f53g6rp9` |
| streaming, Arrow schema fixed | 161 passed, 3 failed; 74.42s | `20261008T163353Z-t24axgzm` |
| streaming, empty artifacts/fixture fixed | 169 passed, 1 failed; 80.32s | `20261008T163548Z-9n8fi439` |
| streaming, deterministic gates | 170 passed; 144.81s; exit 0 | `20261008T163836Z-b_usea99` |
| all, intermediate | 780 passed, 11 failed, 1 skipped; 188.25s | `20261008T163846Z-g8b648iw` |
| streaming, replay/raw normalization | 172 passed; 163.89s; exit 0 | `20261008T164215Z-cf536knw` |
| all, final ledger admission/fallback telemetry | 782 passed, 11 failed, 1 skipped; 205.72s | `20261008T164525Z-3muf8r9k` |

Initial failures exposed Arrow list child renaming, missing empty-stage
checkpoints, a new fixture unpacking error and timing-only test synchronization.
The final synchronization uses Events to force out-of-order NORMALIZE, SCORE
and DECIDE completion. No old assertions weakened. One later test helper was
corrected for the legacy runner's absent cluster_metrics field.

31 new cases: serial/stream business and score identity equivalence at sizes
1/2/4 in both cluster modes, shared census/cross-partition candidates/vetoes,
individual flags/old profiles/fallback, full queue/bytes/backpressure/leases,
consumer timeout/context inheritance, upload/consumer overlap, upload failure,
missing score coverage, empty artifacts, raw/imported StageRunner normalization,
claimed worker execution, staging durability/publication gate and replay.

Ruff changed modules/new tests/runner PASS, except StageRunner's 25 pre-existing
diagnostics: exact code/message multiset unchanged (including DuckDB SQL-bound
variables that Ruff marks unused). Added StageRunner code has no new diagnostics.
Final whole-suite XML comparison: identical 11 baseline failure IDs
(CORS APP_ENV=test and legacy fixtures missing external_id), existing TF-IDF skip.
All 31 new streaming cases pass in the final full suite. Whole-backend suite is
not PASS. `git diff --check` PASS; OneMaster/staged/unrelated state unchanged.

## Pending / Dependencies — Controlled streaming

- Flags remain OFF; live isolated R2/PostgreSQL concurrency/outage rehearsal,
  POSIX leases/fsync, representative shadow data and resource tuning before
  rollout. Local tests do not prove production throughput or remote durability.
- This bounds transport/disk, not full compute memory: normalized snapshot,
  candidate global indexes and cluster state remain in worker memory.
  StageRunner streaming telemetry aggregates compute duration; no persistent
  per-stage telemetry rewrite. Serial/Splink/semantic paths keep old resources.
- Retention/orphan/native resume policy remains separate. Job retries use fresh
  attempt scratch; generation checkpoint idempotence/ownership remains existing.
  Manifest count/metadata limits still fail closed; keep partition capacities
  suitable for the workload. Retained artifacts count against quota.
- External distribution requires source-owner license review (no discovered
  OneMaster license file). No wholesale source copy or runtime imports.

## Commit / Next task — Controlled streaming

Parent `696a6f3`; commit message
`feat(pipeline): add controlled streaming boundaries with global barriers`.
Hash reported after commit. Next task: separately authorized production shadow
equivalence/resource rehearsal and rollout; do not automatically enable flags.

---

Task trước (2026-10-08): partition artifacts/background upload;
xem [PARTITION_ARTIFACTS](PARTITION_ARTIFACTS.md).

## Completed — Partition artifacts / background upload

- Đọc docs/merge, storage Parquet/manifests/checkpoints/ObjectStore, worker/claim/
  generation boundaries và read-only OneMaster artifact.py/background_upload.py;
  SHA-256/provenance trong PARTITION_ARTIFACTS.md. Không runtime import OneMaster.
- Frozen PartitionArtifact; manifest `p119_partition_manifest_v1` pin run/attempt/
  artifact/partition IDs và schema hash. UUID attempt strings của P-119; không
  dùng integer attempt của reference. Manifest mutation OS lock + atomic/fsync.
- Producer ghi bounded tables vào unique temporary Parquet qua capped sink,
  đóng/fsync trước footer/schema/row-count/SHA-256 validation và publication.
  Committed IDs/files không overwrite; queue chỉ directory reference/metadata.
- Background uploader có pending-partition/pending-byte/upload-concurrency/
  local-disk/min-free/admission-timeout limits. Producer reserve trước đọc input;
  pending tính cả writing/queued/active/retry; mỗi encoded/metadata write kiểm
  disk thực + reservations. Retained uploaded files/orphans cũng tính quota.
- R2 partition transfer không dùng nested SDK threads; bounded streaming read-back
  hash/count, immutable content-addressed key và remote receipt riêng. Local
  computation completion, local upload acknowledgment và final durable manifest
  barrier tách nhau. Retry/lost ack/recovery reverify, không blind overwrite.
- OS partition leases bảo vệ readers và queued/active/retry uploads; cleanup chỉ
  sau valid checkpoint và hết lease. Process chết tự release OS lock. Drain
  timeout giữ active lease/workspace ownership đến khi SDK thread kết thúc.
  Một uploader owner cho workspace; không trộn attempts hoặc schema.
- LocalObjectStore hỗ trợ long Windows object paths và exact root containment.
  Baseline runner cleanup long-path report state, kiểm absolute workspace trước
  recursive cleanup; không ignore/suppress cleanup error.
- Không thay stage coordinator, API/frontend, generation JSON protocol, schemaDB
  hay thuật toán. Lớp mới explicit opt-in, chưa bật vào stage pipeline hiện tại.
  Không thêm dependency/broker; OneMaster và untracked ngoài phạm vi giữ nguyên.

## Modified files — Partition artifacts

- New `backend/src/masterone/storage/artifact.py`, `background_upload.py`.
- `backend/src/masterone/storage/object_store.py`, `scripts/run_merge_baseline.py`.
- New `backend/tests/test_partition_artifacts.py`.
- docs/merge PARTITION_ARTIFACTS.md (new), CONTRACTS.md, PROJECT_MAP.md, STATUS.md.

## Tests — Partition artifacts

Python 3.11.9 `.venv`, Windows; isolated temporary SQLite/Parquet/local object
store, spawned lease processes; R2 SDK mocked, real R2/LLM/tracing OFF.
Reports dưới `artifacts/merge-baseline`.

| Command | Executed result | Report |
| --- | --- | --- |
| `--suite all`, pre-task | 720 passed, 11 failed, 1 skipped; 97.09s | `20261008T155530Z-1njszy75` |
| `--suite artifacts`, initial Windows path failures | 77 passed, 8 failed; 44.21s | `20261008T160332Z-ce_la_de` |
| `--suite artifacts`, transfers fixed, runner cleanup failure | 85 passed; 46.60s; command exit 1 during cleanup | `20261008T160554Z-m0aewv18` |
| `--suite artifacts`, workspace lock/long-path cleanup | 87 passed; 47.53s; exit 0 | `20261008T160816Z-a_713de4` |
| `--suite all`, intermediate regression | 747 passed, 11 failed, 1 skipped; 98.77s | `20261008T160826Z-qg5wpywb` |
| `--suite artifacts`, final | 91 passed; 47.84s; exit 0 | `20261008T161545Z-n9bsl5u_` |
| `--suite all`, final | 751 passed, 11 failed, 1 skipped; 99.84s | `20261008T161555Z-tx4qvr5s` |

Intermediate `20261008T161015Z-9a9qx20y` artifacts and
`20261008T161238Z-1tdq3yj9` all were explicitly terminated after a new crash
fixture hung on multiprocessing Event.set following child termination.
Diagnostic `partition-debug` with faulthandler confirmed the synchronization
stack. Fixture no longer reuses that event after killing the process; lease
release assertion retained. Interrupted runs do not count as PASS. Final suite
includes 31 new artifact/upload cases, including actual process death and
checkpoint corruption. No baseline assertions weakened.

Ruff new/changed modules/tests/runner PASS; `git diff --check` PASS.
Whole-backend baseline has 11 existing failures (CORS APP_ENV=test and legacy
StageRunner fixtures missing external_id) and one existing TF-IDF skip.
Final XML comparison against pre-task: exact same 11 failure IDs, no new
failures. Whole-backend suite is not PASS; final targeted artifact suite is PASS.

## Pending / Dependencies — Partition artifacts

- Live isolated R2 durability/multipart/network outage, POSIX lock/fsync and
  representative resource/throughput rehearsal remain before rollout. Tests
  do not prove live R2 or PostgreSQL behavior; no real DB migration/service run.
- Native stage/coordinator adoption is a separate task: pass claim attempt IDs,
  bounded tables, uploader admission hooks and explicit final manifest barrier.
  Existing generation ownership/serving publication gate must remain.
- Retention/orphan/remote-GC policy is separate. Failed manifest publication can
  leave an immutable private orphan; never auto-adopt it as durable checkpoint.
  No fully bounded compute claim; producer input batches remain caller-owned.
- Reference tree has no discovered license file; external distribution needs
  owner licensing review. No wholesale source copy/new runtime dependency.

## Commit / Next task — Partition artifacts

Parent `0069462`; message `feat(storage): add immutable partitions and bounded background uploads`.
Hash reported after commit. Next task: separately authorized native producer
integration and isolated R2/POSIX rehearsal; no automatic deployment.

Task trước (2026-10-08): independent DB queue worker; xem [WORKERS](WORKERS.md).

## Completed — Independent worker / job ownership

- FastAPI không khởi động execution thread/worker và không reset job khi restart.
  API giữ nguyên start/task DTO frontend, thêm cancel với auth/owner/CSRF check.
  Compute/results thuộc process worker riêng, không nằm trong API memory.
- Atomic DB claim, UUID attempt, run/task/worker identity; lease/heartbeat/deadline
  theo DB clock. PostgreSQL claim/recovery dùng run lock SKIP LOCKED; heartbeat
  CAS attempt riêng để không chờ run lock của publication.
- Progress, staging, direct persistence và generation commit kiểm ownership;
  generation attempt ID phải trùng job attempt ID. Validate lại trước commit,
  expired/superseded worker không publish; commit serving/pointer/job/attempt cùng
  transaction. Lost commit acknowledgment không retry job đã COMPLETED.
- Supervisor spawn một execution child, heartbeat độc lập, terminate/kill khi
  cancel/timeout/lost lease. Crash recovery chỉ retire expired/cancelled owners,
  tối đa 3 attempts mặc định, hết budget RETRYABLE_FAILED; explicit start reset
  budget nhưng giữ history/reviewer decisions/golden/audit/receipts.
- Migration 0024 additive ownership columns/index/history, downgrade giữ archive,
  re-upgrade kiểm preservation. Legacy interrupted RUNNING jobs requeue một lần.
  Entrypoint `python -m masterone.mdm.resolution_worker`; Compose worker service
  riêng, có thể scale. Không thêm broker/dependency, không sửa frontend/OneMaster.
- Người dùng đã cho phép đưa worker/API WIP có sẵn vào commit nhiệm vụ này.
  Các artifact/untracked ngoài phạm vi được giữ nguyên, không stage.

## Modified files — Independent worker

- New `mdm/job_queue.py`, migration `0024_job_ownership.py`,
  `backend/tests/test_mdm/test_job_queue.py`, `docs/merge/WORKERS.md`.
- Backend API mdm_runs.py, main.py, mdm engine_adapter.py/generation_staging.py/
  migrate.py/models.py/resolution_persistence.py/resolution_service.py/
  resolution_worker.py; `scripts/run_merge_baseline.py`, `docker-compose.yml`.
- docs/merge CONTRACTS.md, PROJECT_MAP.md, STATUS.md.

## Tests — Independent worker

Python 3.11.9 `.venv`, Windows; disposable SQLite, real spawned processes,
DuckDB/Parquet, mocked storage; R2/LLM/tracing OFF. Reports dưới
`artifacts/merge-baseline`; không mở enterprise DB/service.

| Command | Executed result | Report |
| --- | --- | --- |
| `--suite worker`, WIP baseline | 114 passed; 50.81s | `20261008T154449Z-j_4pygrn` |
| `--suite all`, pre-fix | 716 passed, 11 failed, 1 skipped; 91.83s | `20261008T154533Z-drvbx3i7` |
| `--suite worker`, new fixtures incomplete | 115 passed, 2 failed; 54.04s | `20261008T154713Z-gv8623if` |
| `--suite worker`, heartbeat/attempt/ack fences | 117 passed; 51.81s | `20261008T154926Z-xavj5uil` |
| `--suite all`, ownership regression | 719 passed, 11 failed, 1 skipped; 95.86s | `20261008T155020Z-o9pg9uvl` |
| `--suite worker`, auth fixture used reserved .test domain | 117 passed, 1 failed; 55.52s | `20261008T155123Z-omv79r6d` |
| `--suite all`, final schema/auth regression | 720 passed, 11 failed, 1 skipped; 97.54s | `20261008T155227Z-662kozo9` |
| `--suite worker`, final | 118 passed; 55.96s | `20261008T155326Z-ax26yz_c` |

Intermediate fixture failures: restored monkeypatched lock before cancel;
used promotion to exercise owner check instead of malformed stage payload;
changed test registration domain to example.com. Existing assertions/policy
were not relaxed. Final worker suite includes 23 queue/worker cases; foreign-user
cancel returns 404 and invalid CSRF returns 403, with unchanged task DTO keys.

Ruff changed/new worker/model/service/persistence/staging/adapter/main/migration/
tests/baseline modules PASS. `docker compose config --quiet` PASS;
`git diff --check` PASS. XML comparison pre-fix/regression: same 11 failure IDs,
no new failures; CORS APP_ENV=test (1), legacy StageRunner missing external_id
(10), existing TF-IDF skip (1). Whole-backend suite is not PASS.

## Pending / Dependencies — Independent worker

- PostgreSQL concurrent locking/heartbeat under long publication needs isolated
  rehearsal. Docker executable exists but daemon unavailable; Compose parse is
  not container execution. No production DB migration/deploy or real R2 test.
- SQLite serializes writers; spawn tests prove durable queue/process behavior,
  not PostgreSQL throughput. Worker inputs/proposals/preservation may still use
  RAM; this task removes execution/results from API memory only.
- Stop external workers before rolling back to old embedded-worker runtime;
  retained schema/history is not backward-compatible ownership enforcement.
- Retention/GC, resource limits and representative scale/shadow checks remain
  separate work. The 11 existing whole-backend failures remain out of scope.

## Commit / Next task — Independent worker

Parent `a2427e3`; commit message `feat(mdm): run durable jobs in independent workers`.
Hash reported after commit. Next task: isolated PostgreSQL lease/publication
rehearsal before production rollout; no automatic deploy/migration.

Task trước (2026-10-08): engine/API adapter; xem
[ENGINE_ADAPTER](ENGINE_ADAPTER.md). Các phần phía dưới giữ lịch sử phiên 02–09.

## Completed — Engine/API adapter

- Đọc CONTRACTS, frontend contracts.ts/api.ts/resolutionChoices.ts, lịch sử/diff
  và runtime boundaries phiên 02–09; map commit trong ENGINE_ADAPTER.md.
- EngineResultAdapter dùng tại common serving persistence cho small/large,
  staged/direct runner; score rule units không bị thay bằng Splink probability.
  Evidence giữ state/provenance/tags, loại diagnostic payload, giữ Group A/B/C/D
  và subgroup. Không tạo user decision/cannot-link từ algorithm outcome.
- `MDM_ENGINE_V2_ENABLED=false` giữ trạng thái an toàn; run mới opt-in pin profile
  person v2 (decision/cluster/staging cùng bật). Serving run hiện hữu được
  grandfather vào current profile; retry/manual API dùng config đã lưu.
  ContextVar request/execution không mutate global Settings. Sparse retrieval
  và calibrated Splink không tự bật theo flag này.
- Migration 0023 thêm engine selection table riêng, không đổi DTO/run columns;
  downgrade giữ selection rows, re-upgrade kiểm thử với dữ liệu đã lưu.
- Giữ review queue/detail/batch, auto/manual merge, golden, audit/export/preview
  và rollback routes. JSON text/plain từ api.ts được normalize ở resolution
  router, giữ schema/CSRF/ownership/revision validation; không đổi UI/upload.
- Golden GET/CSV/event snapshot áp dụng saved override; override ưu tiên hơn
  submitted source choice cùng field. PUT increment proposal revision để
  invalidate preview, khóa run/proposal cùng protocol manual merge/publication;
  explicit clear dựng lại base golden, retry không clear.
- Queue pagination giữ offset/limit 1..100; detail SQL ordered/bounded, explicit
  413 trên 200 members/2000 pairs, không truncate. Group graph chưa được rewrite
  vì không được paginate raw edge trước khi dựng connected groups.
- Không sửa frontend/OneMaster, không chạy real DB migration/R2/service.

## Modified files — Engine/API adapter

- New mdm/engine_adapter.py, migration 0023_engine_selection.py.
- config.py, mdm/models.py/migrate.py/generation_staging.py/resolution_service.py,
  resolution_persistence.py/resolution_items.py/resolution_preview.py,
  pipeline/decision_semantics.py/cluster_policy.py/stage_runner.py,
  api/mdm_resolution.py dưới backend/src/masterone.
- New backend/tests/test_mdm/test_engine_adapter.py (30 cases),
  scripts/smoke_frontend_api.mjs; scripts/run_merge_baseline.py adapter suite.
- docs/merge/ENGINE_ADAPTER.md (new), CONTRACTS.md, PROJECT_MAP.md, STATUS.md.

## Tests — Engine/API adapter

Python 3.11.9 `.venv`, isolated SQLite/DuckDB/Parquet; Node 24.21.0; real
R2/LLM/tracing OFF. Loopback bridge to disposable authenticated TestClient,
không mở app lifespan hoặc DB doanh nghiệp. Reports: artifacts/merge-baseline.

| Command | Executed result | Report |
| --- | --- | --- |
| `--suite all`, pre-task | 667 passed, 11 failed, 1 skipped; 67.74s | `20261008T145412Z-ctzfxkmk` |
| `--suite adapter`, initial | 175 passed; 30.89s | `20261008T150238Z-j0i2yrft` |
| `--suite adapter`, actual api.ts smoke/clear/transport | 179 passed; 32.43s | `20261008T150822Z-pt0nrg16` |
| `--suite adapter`, strong auto fixture corrected | 180 passed; 32.16s | `20261008T151053Z-tdwbmlsn` |
| `--suite all`, source-choice fixture invalid | 696 passed, 12 failed, 1 skipped; 67.64s | `20261008T151239Z-czgz_f3q` |
| `--suite adapter`, categories/source-choice fixture corrected | 184 passed; 33.61s | `20261008T151403Z-pfyxwplo` |
| `--suite all`, regression | 697 passed, 11 failed, 1 skipped; 68.33s | `20261008T151459Z-xmrn0p06` |
| `--suite adapter`, golden event snapshot | 184 passed; 33.81s | `20261008T151622Z-9xdaysy_` |
| `--suite adapter`, final golden edit locking | 184 passed; 33.00s | `20261008T151802Z-hjmgx31j` |

Intermediate `20261008T151002Z-0u_mebmc`: 179 passed/1 failed (new fixture had
insufficient evidence for AUTO_READY); corrected fixture uses strong matching
name/contact/DOB/address. Source-choice fixture needed a distinct source value
to be an allowed choice; corrected without relaxing backend validation/policy.
Whole-backend failure IDs compared to pre-task XML: identical 11; new failures
none. Final adapter suite revalidated after golden snapshot assertions/locking.

Ruff new adapter/tests/migration and changed config/model/semantics/service/
staging/persistence modules PASS; import checks on modified legacy API/items/
preview/runner and baseline runner PASS. Actual unchanged api.ts smoke PASS:
health/overview/pagination/detail/no_merge/audit. It is a consumer transport
smoke, not React render/build/browser verification. `npm.cmd test -- --reporter=dot`
in frontend BLOCKED: vitest executable absent (node_modules not installed).

## Pending / Dependencies — Engine/API adapter

- Whole-backend baseline failures: one local-dev CORS expectation under
  APP_ENV=test, ten legacy StageRunner fixtures missing external_id; existing
  TF-IDF partition skip. No baseline assertions changed to hide these.
- PostgreSQL multi-worker locking/lease rehearsal, real R2, representative
  shadow/canary recall and resource/API-scale measurements remain required
  before production rollout. Engine/profile pinning is not a worker lease.
- Detail count guards do not cap arbitrary raw-field bytes; grouped queue
  still builds global graph. Further scale work needs a business-equivalent
  grouped index, not raw-edge pagination.
- Full frontend Vitest/Next/browser smoke requires installed frontend deps;
  Node consumer smoke does not claim browser cookie/CORS or React coverage.

## Commit / Next task — Engine/API adapter

Commit: `feat(mdm): adapt engine results to existing API contracts` (hash reported
after commit). Parent
`b547036`; engine v2 default OFF. No production migration or switch performed.
Next task: separately authorized shadow/equivalence and rollout validation;
không tự triển khai frontend changes hoặc production canary.

Task trước (2026-10-08): generation staging và atomic publication;
xem [GENERATIONS](GENERATIONS.md). Clustering đã commit `5717d9b`; decision
semantics đã commit `f3d6aea`. Các phần bên dưới giữ lịch sử và baseline.

## Completed — Generation staging/publication

- Đọc docs/merge, ORM/migration graph, storage/checkpoint/object-store, worker và
  small/large/direct materialization; tham chiếu OneMaster generation_staging.py
  và materialize.py (SHA-256 trong GENERATIONS.md). Không sửa OneMaster/frontend.
- Generation UUIDv5 pin run/input/reviewer/config/code/storage; attempt riêng,
  fence theo run lock, supersede stale attempt, failure không sửa newer owner.
- Migration 0022 thêm năm bảng, không thêm cột vào legacy ResolutionRun/serving:
  generations, attempts, publications, staged_results, partition_receipts.
  Staging typed projections pair/proposal/membership/golden/review theo batch;
  unique/record FK/order constraints; checkpoint/receipt/hash cùng transaction.
- Artifact DB bytes hoặc R2 generation/hash key, upload/read-back trước receipt;
  kiểm lại khi promote. Không chấp nhận local-ready Parquet thay durable receipt.
  Seal manifest đủ mọi kinds, counts/candidate coverage và partition checksums.
- Validate toàn payload/receipt/hash/count/reference, disjoint membership,
  proposal/golden coverage/provenance, review/edge references, source/reviewer
  fingerprint/base active pointer. Không promote STAGING/incomplete/corrupt data.
- Promote serving reconciliation + pointer/generation/attempt/task/run/counts
  trong một DB transaction. Reader khác không thấy flushed uncommitted rows.
  Retry partition/promotion idempotent; lost acknowledgment sau commit vẫn thành
  công. Crash/upload/stale/input-change giữ serving và active generation trước.
- Giữ insert-only preservation contract: all saved pair/proposal/case rows,
  reviewer decisions/revisions, golden overrides, audit/history/entities/exports.
  Không coi generation là destructive recompute. New proposals không chồng lên
  saved/reviewer members. Existing protected runs luôn giữ publication gate.
- Default `GENERATION_STAGING_ENABLED=false`, batch 512 (1..2048). SQLite writer
  reservation hỗ trợ tests; PostgreSQL run row lock chưa rehearsal production.
  Downgrade giữ additive tables/checkpoints/pointers; re-upgrade không mất dữ liệu.

## Modified files — Generation staging/publication

- `backend/src/masterone/mdm/generation_staging.py` (mới).
- `backend/migrations/versions/0022_generation_staging.py` (mới), mdm/migrate.py.
- config.py, mdm/models.py, mdm/resolution_service.py, resolution_persistence.py,
  resolution_worker.py, pipeline/stage_runner.py dưới backend/src/masterone.
- `backend/tests/test_mdm/test_generation_staging.py` (mới, 33 cases).
- scripts/run_merge_baseline.py (generation suite, isolated flag).
- docs/merge/GENERATIONS.md (mới), CONTRACTS.md, PROJECT_MAP.md, STATUS.md.

## Tests — Generation staging/publication

`.venv`, Python 3.11.9/Windows; isolated temporary SQLite (new fixtures foreign
keys ON), DuckDB/Parquet, mocked object storage; R2/LLM/tracing OFF. Reports dưới
artifacts/merge-baseline (ignored). Không chạy enterprise DB/service/lifespan.

| Command | Executed result | Report |
| --- | --- | --- |
| `scripts/run_merge_baseline.py --suite all`, pre-task clustering final | 634 passed, 11 failed, 1 skipped; 54.18s | `20261008T142059Z-pqnf5u9h` |
| `--suite generation`, intermediate | 228 passed; 33.82s | `20261008T144143Z-kapppkj4` |
| `--suite generation`, fencing/direct publication | 228 passed; 33.12s | `20261008T144434Z-jk_mooc9` |
| `--suite generation`, reader isolation/migration history | 233 passed; 36.38s | `20261008T144609Z-_743am9x` |
| `--suite all`, before safe SQL error codes | 667 passed, 11 failed, 1 skipped; 67.94s | `20261008T144746Z-oubb3lzl` |
| `--suite generation`, final safe error codes | 233 passed; 37.96s | `20261008T144837Z-fiv99mvi` |
| `--suite all`, final | 667 passed, 11 failed, 1 skipped; 66.89s | `20261008T145104Z-jldpvzrl` |
| Ruff new module/migration/tests + config/model/service/persistence/worker/runner, import check StageRunner; `git diff --check` | PASS | Tool output |

Exploratory red runs: first 199 passed/2 failed/27 fixture errors (missing file FK
and new Run columns incompatible with pre-0022 fixtures); moved publication
pointers to a separate table, fixed new fixture file FK. Next 227 passed/1 failed
(new test expected 9 rows instead of actual 8); corrected expected fixture count.
Không sửa existing migration assertions/fixtures, không suppress failure.

11 full-suite failures/1 skip vẫn đúng baseline: CORS APP_ENV=test (1), legacy
StageRunner fixtures thiếu external_id tại CLUSTER (10), TF-IDF partition skip (1).
Full suite không PASS. Test corruption/duplicate error dùng safe code, SQL JSON
parameters không xuất vào lỗi worker của path mới. Không gọi mock R2 là live R2.

## Pending / Dependencies — Generation staging/publication

- PostgreSQL/Neon isolated migration/locking/rollback/reader isolation rehearsal,
  R2 isolated durability rehearsal và shadow/scale/storage-cost checks trước ON.
- Distributed worker claim/lease, startup ownership và compute cancellation chưa
  hoàn tất; publication fence chặn stale writes, không ngăn duplicate compute.
- Retention/GC và R2 orphan cleanup cần policy riêng. Private rows/receipts giữ
  để audit/retry; downgrade không tự xóa chúng. Không tự deploy/migrate DB thật.
- Preservation maps và input/candidates/proposals vẫn RAM; optional semantic
  enrichment giữ global decision list. Validation đọc batches/keyset, manifest
  metadata O(partitions); không tuyên bố worker bounded/production throughput.
- Không thêm dependency. Rollback runtime cũ chỉ read/review protected runs;
  không rerun bằng publisher cũ. Không thay person/organization domain semantics.

## Commit / Next task — Generation staging/publication

Parent `5717d9b`; message `feat(mdm): stage durable generations before atomic publication`.
Hash báo final. Next task đề xuất: isolated PostgreSQL/R2 rehearsal và shadow
validation trước bật flag; không tự thực thi trên DB doanh nghiệp.

Task trước (2026-10-08): safe clustering/golden; commit `5717d9b`;
xem [CLUSTERING](CLUSTERING.md).

## Completed — Safe clustering/golden

- Giữ constrained union-find; complete DECIDE barrier và sorted disk cursor theo
  weight DESC NULLS LAST, record_id_l/r ASC; không union theo batch completion.
- Component guard kiểm mọi pair, direct evidence/cannot-link/review veto riêng,
  size/per-union/per-run bounds và disk cache. Policy uniqueness theo source/domain;
  same-source duplicates hợp lệ khi không bật uniqueness. Adapter giữ legacy.
- Golden giữ multiple contacts, selected field provenance/all sources/alternatives;
  source priority, verification, quality, actual aware timestamp, deterministic tie.
  Retry và manual expansion giữ saved override; conflicting override bị chặn.
- Cùng small/StageRunner path; không reload scored edges thành list khi semantic
  enrichment OFF. Records/candidates/proposals vẫn dùng RAM, không nhận là worker
  bounded hoàn toàn. New flag mặc định OFF, cần decision v2 ON. Không sửa frontend,
  OneMaster, history/exports/audit hoặc schema serving.

## Modified files — Safe clustering/golden

- Config.py, pyproject.toml và mdm/config/clustering_person_v2.yaml.
- Pipeline cluster_policy.py, decision_store.py, golden_v2.py (mới),
  cluster_guards.py, clustering.py, golden.py, stage_runner.py.
- MDM resolution_service.py, resolution_items.py, resolution_pairs.py,
  resolution_review.py; scripts/run_merge_baseline.py.
- test_safe_clustering.py (15 additional cases), docs/merge/CLUSTERING.md, STATUS.md.

## Tests — Safe clustering/golden

Python 3.11.9/Windows/.venv; isolated runner, temporary SQLite/DuckDB/Parquet,
R2/LLM/tracing OFF. Reports dưới artifacts/merge-baseline (ignored).

| Command | Executed result | Report |
| --- | --- | --- |
| `scripts/run_merge_baseline.py --suite all`, before | 619 passed, 11 failed, 1 skipped; 46.82s | `20261008T113532Z-bzgfmotl` |
| `--suite clustering`, final | 174 passed; 22.56s | `20261008T141836Z-ja17cf3t` |
| `--suite all`, final | 634 passed, 11 failed, 1 skipped; 54.18s | `20261008T142059Z-pqnf5u9h` |
| Ruff new modules/tests/runner; `git diff --check` | PASS | Tool output |

11 failures/1 skip giống baseline: CORS APP_ENV=test (1), legacy StageRunner
fixtures thiếu external_id (10), TF-IDF partition skip (1). Không sửa/suppress
assertions cũ; full suite không PASS. Không chạy PostgreSQL/R2/LLM thật.

## Pending / Dependencies — Safe clustering/golden

- Shadow/equivalence và scale validation trước đổi default; PostgreSQL rehearsal.
- Optional semantic enrichment vẫn giữ decision list; không thay budget/cache
  contract trong task này. New decision cursor owner phải close sau consumption.
- Worker ownership và durable generation publication là task tiếp theo đã giao.
- Không thêm dependency; package-data thêm YAML policy. OneMaster source hash
  trong CLUSTERING.md, không tuyên bố license/distribution approval.

## Commit / Next task — Safe clustering/golden

Parent `f3d6aea`; commit message `feat(mdm): guard deterministic clusters and preserve golden provenance`.
Hash báo final. Tiếp theo: generation/attempt staging, durable checkpoint,
validation và atomic promotion theo yêu cầu người dùng; giữ review/golden state.

Task mới nhất (2026-10-08): Person decision semantics/evaluator và bảo toàn
review qua retry; xem [DECISIONS](DECISIONS.md). Các phần ScoringEngine,
sparse TF-IDF/CandidateEngine/input bên dưới là lịch sử task trước.

## Completed — Decision semantics

- Đọc docs/merge, pair/case/proposal/negative-edge models, active và legacy
  review, preview/export, policy/floor/hard constraints/cluster guards và hai
  nhánh pipeline. Tham chiếu OneMaster evaluator_v2.py, preprocessing.py,
  contracts_v2.py; hashes trong DECISIONS.md. Xác minh roots/status/staged diff
  cả hai repo; giữ nguyên WIP/untracked, không sửa OneMaster/frontend.
- Tách PairEvidence, HardConstraints, PairEvaluation, MergeEligibility,
  ReviewDecision. Một evaluator, adapter giữ groups/subgroups, normalization
  tags (Group B), floor, YAML policies và rule-score units hiện tại.
- REJECTED là execution veto riêng, không tạo cannot-link/NON_MATCH. BLOCKED,
  DEFERRED và NOT_MERGEABLE không chứng minh NON_MATCH. Historical explicit
  NON_MATCH giữ nguyên và được nhận diện nhất quán ở review/items/preview.
  Active negative edges được đọc riêng, không backfill từ rejected proposals.
- Opt-in person v2: contact khác nhau kể cả verified vẫn là DF; contact trùng
  kiểm shared/frequency/copied/historical risk. Verified usable DOB conflict
  khác unverified difference; unverified CF vẫn giữ subgroup D tương ứng.
  Government ID xét VALID, type, explicit namespace, verification và domain
  uniqueness/single-value metadata. Không áp person v2 cho ORGANIZATION.
- Corrected semantics mặc định OFF theo AGENT_RULES rule 9:
  `DECISION_SEMANTICS_V2_ENABLED=false`. Setting và source được pin trong SCORE
  identity; đổi giữa run fail rõ ràng. Legacy adapter vẫn chạy được khi OFF.
- Manual approvals dùng evaluator hiện tại cho mọi internal pair sau expansion,
  kể cả không có candidate edge; soft auto policy vẫn có thể được steward duyệt.
  Cluster guard cũng kiểm person constraints xuyên member. Pair preprocessing
  theo batch không union; final union dùng global weight DESC/pair ID ASC.
- Persistence small/large/direct StageRunner dùng chung ORM transaction với
  run lock; retry insert-only map canonical pair identities, giữ nguyên mọi
  serving row cũ/revision/reviewer state/golden/case/audit/history. New auto
  proposals không đè hoặc chồng lên saved proposals/reviewer-owned members.
  Bỏ serving INSERT riêng qua DuckDB và không reset thành UNDECIDED khi retry.
- Không sửa assertions/test cũ để làm xanh; 52 tests mới kiểm decision meaning,
  namespace/risk/manual parity/replay, nhóm/subgroup và batch-order regression.

## Modified files — Decision semantics

- `backend/src/masterone/config.py` (flag default OFF)
- `backend/src/masterone/mdm/pipeline/decision_semantics.py` (mới)
- `pipeline/matching.py`, `hard_constraints.py`, `cluster_guards.py`,
  `clustering.py`, `scoring_engine.py`, `stage_runner.py` dưới cùng mdm directory
- `backend/src/masterone/mdm/resolution_persistence.py` (mới)
- `resolution_service.py`, `resolution_items.py`, `resolution_pairs.py`,
  `resolution_review.py`, `resolution_preview.py` dưới cùng mdm directory
- `backend/tests/test_mdm/test_decision_semantics.py` (mới)
- `scripts/run_merge_baseline.py` (decisions suite, isolate flag)
- `docs/merge/DECISIONS.md` (mới), `CONTRACTS.md`, `PROJECT_MAP.md`,
  `SCORING.md`, `STATUS.md`

## Tests — Decision semantics

`.venv`, Python 3.11.9/Windows; runner dùng disposable SQLite/Parquet/DuckDB,
R2/LLM/tracing OFF. Các tests v2 bật flag bằng fixture và restore sau test.
Không dùng DB/services thật. Reports dưới `artifacts/merge-baseline/` (ignored).

| Command | Kết quả thực thi | Report directory |
| --- | --- | --- |
| `scripts/run_merge_baseline.py --suite all`, trước sửa | 567 passed, 11 failed, 1 skipped; 46.52s; exit 1 | `20261008T111701Z-g21szepg` |
| `--suite decisions`, intermediate | 239 passed; 13.98s; exit 0 | `20261008T112432Z-pi6lpvqi` |
| `--suite decisions`, contact/cluster hardening | 247 passed; 14.24s; exit 0 | `20261008T112609Z-hqf1cm50` |
| `--suite decisions`, durable StageRunner retry | 248 passed; 14.48s; exit 0 | `20261008T112807Z-o98yp7s3` |
| `--suite all`, durable retry | 618 passed, 11 failed, 1 skipped; 46.33s; exit 1 | `20261008T112931Z-stxrvsg4` |
| `--suite decisions`, final | 249 passed; 14.32s; exit 0 | `20261008T113052Z-uxu4s3ml` |
| `--suite all`, final | 619 passed, 11 failed, 1 skipped; 46.66s; exit 1 | `20261008T113224Z-v91d67at` |
| Ruff new contracts/persistence/tests, matching/scoring/service/items/pairs/preview/runner | All checks passed; exit 0 | Tool output |
| Ruff import sorting các files đã sửa | PASS; exit 0 | Tool output |
| `git diff --check` | PASS | Tool output |

Các lượt intermediate đỏ: fixture mới thiếu audit actor, duplicate email kwarg,
score thấp hơn policy threshold, và SQLite memory engine thứ hai là DB khác.
Đã sửa fixtures; test StageRunner retry dùng DB file disposable thật. Không
skip/xfail các lỗi này. Final focused/full results ở bảng trên.

11 failures còn lại trùng baseline: CORS development-origin test trong APP_ENV=test
(1) và fixture StageRunner thiếu external_id/source metadata tại CLUSTER (10).
Existing TF-IDF partition-key test vẫn skip (1). Không gọi full suite là PASS.
Ruff toàn legacy files còn whitespace/one-line statements/DuckDB replacement
scan false-positive và unused variable cũ; không mở rộng cleanup ngoài task.

## Pending — Decision semantics

- Representative shadow/equivalence trên corpus production trước bật default.
- Authoritative input metadata contract cho verification/namespace; không suy
  verified/namespace từ syntax hay extra CSV columns. Unsupported/custom ID
  domain cần uniqueness/single-value contract riêng.
- PostgreSQL concurrency/row-lock và 10k+ publication throughput/RSS chưa test.
  ORM preservation thay SQL INSERT có thể khác hiệu năng; không có claim scale.
- Retry giữ serving rows cũ một cách bảo thủ, không phải reclassification hoặc
  generation staging/publication protocol; reconciliation cần task riêng để
  giữ reviewer state/history và invalidation previews.
- Organization domain contract chưa có; không mở rộng person evaluator.
- Baseline failures nêu trên giữ nguyên; frontend/R2/LLM/live DB không kiểm định.

## Dependencies — Decision semantics

- Không thêm dependency; dùng P-119 settings/ORM/rule policy/Parquet hiện có.
- Input verification/namespace và policy rollout cần chủ sở hữu dữ liệu xác nhận
  metadata authoritative; OneMaster license chưa xác nhận, không copy source.
- Rollback corrected person semantics: flag false và fresh artifacts. Không
  rollback/xóa saved review decisions, negative edges, golden hay audit.

## Commit — Decision semantics

Commit chứa section này: `Unify person decision semantics and preserve review on retry`.
Tra SHA bằng `git log -1 --format=%H -- docs/merge/STATUS.md`; final response ghi SHA.

## Next task — Decision semantics

Shadow đánh giá semantics v2 với authoritative verification/namespace metadata;
kiểm PostgreSQL publication/concurrency trước rollout. Không tự mở rộng backlog.

## Completed — ScoringEngine

- Đọc docs/merge, CandidateEngine, toàn bộ pipeline SCORE small/large,
  matching/hard constraints/floor/policy, semantic consumer và display fallback
  resolution_items; tham chiếu OneMaster candidate_scoring.py/splink_adapter.py.
  Xác minh roots/status/staged diff cả hai repo; OneMaster chỉ đọc, giữ WIP cũ.
- Bỏ import-Splink rồi `pass`. ScoringEngine/ScoreResult/config chung cho
  `_compute`, StageRunner SCORE và run_pipeline; rule scorer vẫn là mặc định.
- Full normalized snapshot frequency census, cùng scalar primary phone/email
  record semantics với baseline small; large không còn candidate-only census.
- Evidence/numeric scoring tách khỏi DECIDE. Giữ score_pair compatibility
  facade, nguyên hard-negative, floor/subgroup/policy semantics và unrounded
  threshold checks. Rule score không đổi tên thành probability.
- Mọi canonical candidate đều có SCORE row, gồm insufficient/hard conflicts;
  DECIDE mới lọc serving output, insufficient_count giữ rõ ràng.
- SCORE hydrate/write theo batch, typed Parquet + sidecar identity/coverage;
  SQLite disk ledger kiểm duplicate xuyên batch, per-batch ID sets và final
  metadata count phát hiện missing/extra pairs. Failure không publish partial
  Parquet hoặc ghi đè artifact complete trước đó.
- Frozen config, implementation/model version/hash, policy/negative/snapshot/
  frequency hashes và dependency versions. Loaded policies/model được copy;
  snapshot/source recheck trước finalize. Existing run artifact có identity
  khác bị chặn thay vì rewrite config/model ngầm.
- Adapter Splink 5.0.0 thực sự dùng full snapshot, registered blocked pairs và
  predict từng batch, không training/threshold; cleanup SQL cache và scratch DB.
  **Splink chưa kích hoạt**: không có P-119 trained/calibrated/evaluated model
  hợp lệ. Missing/incompatible/uncalibrated manifest/version/hash fail rõ ràng,
  không fallback rule và không tự train hoặc đặt calibration.
- Rule baseline và real Splink adapter arithmetic tests đã chạy offline.
  Fixture Splink chỉ test math/API, không phải trained production model.

## Modified files — ScoringEngine

- `backend/src/masterone/mdm/pipeline/scoring_engine.py` (mới)
- `backend/src/masterone/mdm/pipeline/matching.py`, `policy_engine.py`
- `backend/src/masterone/mdm/pipeline/stage_runner.py`
- `backend/src/masterone/mdm/resolution_service.py`
- `backend/tests/test_mdm/test_scoring_engine.py` (mới, 24 cases)
- `scripts/run_merge_baseline.py` (scoring suite; TEMP/TMP/TMPDIR isolated)
- `docs/merge/SCORING.md` (mới), `CANDIDATES.md`, `CONTRACTS.md`, `PROJECT_MAP.md`, `STATUS.md`

## Tests — ScoringEngine

`.venv`, Python 3.11.9/Windows; isolated SQLite, R2/LLM/tracing disabled,
score/splink scratch trong disposable runner directory. Không dùng services/DB
thật, không train model. Reports dưới artifacts/merge-baseline (ignored).

| Command | Kết quả thực thi | Report directory |
| --- | --- | --- |
| `scripts/run_merge_baseline.py --suite all`, trước sửa | 543 passed, 11 failed, 1 skipped; 39.57s; exit 1 | `20261008T104813Z-_r2zu_li` |
| `--suite scoring`, intermediate | 186 passed; 6.82s; exit 0 | `20261008T110002Z-2pqfp30u` |
| `--suite scoring`, full TF/frozen model test | 187 passed; 7.13s; exit 0 | `20261008T110207Z-4zzu40yp` |
| `--suite scoring`, final focused | 188 passed; 6.89s; exit 0 | `20261008T110606Z-kko3i6hh` |
| `--suite all`, intermediate | 565 passed, 11 failed, 1 skipped; 42.66s; exit 1 | `20261008T110043Z-eaui_t9d` |
| `--suite all`, identity/coverage hardening | 567 passed, 11 failed, 1 skipped; 43.51s; exit 1 | `20261008T110346Z-fh7x0v22` |
| `--suite all`, final | 567 passed, 11 failed, 1 skipped; 43.87s; exit 1 | `20261008T110850Z-m9e1hqv9` |
| Ruff scoring_engine.py/test_scoring_engine.py/matching.py/runner | All checks passed; exit 0 | Tool output |
| New imports stage_runner.py/resolution_service.py | Sorted by Ruff | Tool output |
| `git diff --check` | PASS | Tool output |

24 new cases pass: same scoring/identity/census small vs large across batch
sizes; full pipeline artifact + pairs equality; full corpus shared frequency;
rule baseline facade; SCORE insufficient/hard-negative retention; 4,950 pairs
streamed in 292 batches, max 17 rows and 34 hydrated endpoints; missing/extra/
duplicate/reversed/dangling/count coverage failures preserve old output; frozen
config/policies, repeat/shuffled stability; snapshot mutation rejection; empty
typed artifact; model readiness blockers; real registered Splink prediction
batches/cache cleanup; real full-snapshot native TF and frozen loaded settings.

**Full suite không PASS.** 11 failure identities giống baseline: một CORS
expectation APP_ENV=test và mười StageRunner fixtures thiếu external_id/source_system.
SCORE hiện qua được projection mới; fixture lỗi bộc lộ ở CLUSTER external_id.
Existing partition_keys skip giữ nguyên, không tính PASS. Không sửa assertion
legacy hoặc suppress failure. Exploratory candidates run `20261008T105623Z-s8csdmfp`
có 2 failures do DuckDB cursor không thấy TEMP VIEW; đã sửa direct Parquet reads.
Exploratory scoring `20261008T105904Z-knfjyxas` có 1 failure cleanup nhầm VIEW là
TABLE; đã sửa và rerun tất cả real Splink tests PASS.

## Pending / Dependencies / Blocker — ScoringEngine

- **Blocker Splink production:** thiếu model calibrated/evaluated đúng P-119
  normalization, fitted parameters và manifest/hash/version/provenance. Inventory
  ignored model JSON P-119 benchmark/eval/artifacts: không có. OneMaster performance
  manifests được đọc ghi unestimated parameters, "Scores are not calibrated",
  normalization `vn_normalization_v1`; không dùng chúng cho production P-119.
- Manifest attestation cần owner cung cấp cùng evaluation report; runtime
  không tự xác nhận calibration. Không chọn probability decision threshold.
  Next: frozen compatible artifact + representative labeled shadow evaluation.
- Chỉ SCORE batch outputs/endpoint hydration bounded; full frequency Counters,
  CandidateEngine, DECIDE decisions/semantic consumer và CLUSTER vẫn RAM. Chưa
  đo/chứng minh whole-pipeline RSS hoặc production throughput trong task này.
- Small artifacts default unique OS temp directory được retained; production
  caller cần chỉ định scoring_output_dir/retention. Parquet/sidecar local replace
  chưa phải multi-artifact publication/checkpoint protocol. No DB model identity
  migration; identity hiện nằm artifact/StageResult/returned scoring_metrics.
- 11 baseline failures, downstream spill, worker ownership, human replay và
  durable publication ngoài scope. Không thay UI/review/golden/audit/persistence.
- Không thêm dependency, không copy/import source ngoài repo. OneMaster LICENSE
  chưa xác nhận; reference SHA-256 ghi trong SCORING.md. Adapter pins installed
  Splink 5.0.0; production dependency lock/rehearsal vẫn cần kiểm định.
- stage_runner.py/policy_engine.py/resolution_service.py còn legacy lint errors
  (style và DuckDB replacement-scan variables); không tuyên bố whole-file lint PASS.

## Commit / Next task — ScoringEngine

Commit là commit chứa phần STATUS này (`git log -1` sau khi commit). Next task:
cung cấp frozen P-119 Splink model + calibration/evaluation provenance và chạy
shadow trước activation; probability decision policy/durable storage/streaming
downstream là task riêng, chưa triển khai ngoài phạm vi hiện tại.

Task trước (2026-10-08): global sparse Top-K TF-IDF. Chi tiết thiết kế,
configuration, approval/rollback và semantics: [SPARSE_TFIDF](SPARSE_TFIDF.md).
Các phần CandidateEngine/input bên dưới là lịch sử các task trước.

## Completed — Sparse TF-IDF

- Đọc docs/merge, CandidateEngine phiên trước, P-119 TF-IDF và OneMaster
  candidates/tfidf.py + planner.py; xác minh roots/status/staged diff cả hai repo.
  Chỉ sửa P-119, giữ nguyên dirty tree OneMaster và untracked/WIP của người dùng.
- GlobalTfidfIndex full snapshot; char_wb 3–4 + Vietnamese folding, IDF tính từ
  toàn record corpus kể cả record rỗng; duplicate text vectors nén nhưng record
  document frequency không đổi. Không gate fuzzy bằng DOB/tỉnh/source/chunk.
- Sparse CSR similarity tiles, query/target batch, record Top-K, deterministic
  score/ID ties; streaming observations vào CandidateEngine union cùng provenance.
  Có name/address config. Legacy rollback không còn dense similarity block;
  chỉ giữ dense row O(N) để bảo toàn argpartition ties cũ.
- Workload planner block sizes/eligible pairs, conservative memory estimate,
  warning/approval/rejection, snapshot/config-bound plan ID, hard operation/cell
  caps; không truncate hoặc âm thầm đổi K/threshold. Actual unique pair cap giữ nguyên.
- Flag `CandidateConfig(sparse_tfidf=True)` opt-in theo AGENT_RULES rule 9;
  chưa bật default production. `_compute`/StageRunner nhận cùng config đã có.
- Benchmark offline fresh processes đo OS peak working set và labeled synthetic
  candidate recall. Không tuyên bố nhanh hơn; chưa có comparison legacy throughput.

## Modified files — Sparse TF-IDF

- `backend/src/masterone/mdm/pipeline/tfidf_blocking.py`
- `backend/src/masterone/mdm/pipeline/candidate_planner.py` (mới)
- `backend/src/masterone/mdm/pipeline/candidate_engine.py`
- `backend/tests/test_mdm/test_sparse_tfidf.py` (mới, 16 cases)
- `scripts/benchmark_sparse_tfidf.py` (mới), `scripts/run_merge_baseline.py`
- `.gitignore` (chỉ bỏ qua generated sparse benchmark reports)
- `docs/merge/SPARSE_TFIDF.md` (mới), `CANDIDATES.md`, `PROJECT_MAP.md`, `STATUS.md`

## Tests — Sparse TF-IDF

`.venv`, Python 3.11.9, Windows; isolated runner SQLite tạm, R2/LLM/tracing
tắt. Không gọi service/DB thật. Ruff tất cả sáu Python files thay đổi PASS;
`git diff --check` PASS. Reports generated/ignored dưới artifacts/merge-baseline.

- Trước sửa: `scripts/run_merge_baseline.py --suite all`: **527 passed,
  11 failed, 1 skipped**, 39.95s, exit 1; report `20261008T103240Z-8p8agqsg`.
- Focused intermediate `--suite candidates`: **79 passed, 1 skipped**,
  3.85s, exit 0; report `20261008T103557Z-q72u3sib` (14 sparse cases lúc đó).
- Full intermediate: **541 passed, 11 failed, 1 skipped**, 40.16s, exit 1;
  report `20261008T103749Z-nsxgzsnq`. Thêm cell-limit/actual-cap contracts sau run.
- Full final `--suite all`: **543 passed, 11 failed, 1 skipped**, 40.21s,
  exit 1; report `20261008T104000Z-7rvswo_d`; đủ 16 sparse cases PASS.

Các failures full suite là cùng 11 identities baseline: một CORS expectation
APP_ENV=test, mười fixtures StageRunner thiếu source_system/external_id. Existing
`_partition_keys` skip giữ nguyên. Không sửa assertion cũ để làm xanh.

Benchmark command: `.venv/Scripts/python.exe -X utf8 scripts/benchmark_sparse_tfidf.py`,
exit 0. Seed119: typo pairs unique hoặc nhóm 20 record, gold mọi within-entity
pairs; first group tiếng Việt/cross-partition. Top-K=10/.30; engine benchmark
tắt grams/composites, giữ exact blocks. Peak RSS là toàn fresh child, bao gồm
imports, records/index/union; TF-IDF và engine chạy tuần tự trong cùng child.

| Distribution | Records | Peak RSS bytes | Total seconds | TF-IDF recall | Engine recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unique typo pairs | 1,000 | 159932416 | 2.101 | 1.000000 | 1.000000 |
| Unique typo pairs | 5,000 | 191713280 | 4.799 | 1.000000 | 1.000000 |
| Unique typo pairs | 10,000 | 228798464 | 11.703 | 1.000000 | 1.000000 |
| Duplicate groups | 1,000 | 156631040 | 1.657 | 0.763158 | 0.999053 |
| Duplicate groups | 5,000 | 188825600 | 2.176 | 0.763158 | 0.999811 |
| Duplicate groups | 10,000 | 227586048 | 2.695 | 0.763158 | 0.999905 |

Không gọi synthetic recall là production recall. Top-K=10 không bao phủ mọi
cặp trong nhóm >K; exact pass bù phần lớn nhưng không toàn bộ first group
Vietnamese variants (18 missed gold pairs mỗi duplicate workload). Full report
`artifacts/sparse-tfidf/benchmark.json` có plan/nnz/emissions/timing/platform.

## Pending / Dependencies — Sparse TF-IDF

- Cần shadow/canary trên labeled representative production corpus, chọn K,
  threshold và budgets bằng recall/RSS thực tế trước khi bật default.
- Memory budget là allocation estimate, không OS-enforced RSS limit. Snapshot,
  blocks, logs, index và union vẫn in-memory; external spill/index là task riêng.
  Workloads vượt caps fail rõ ràng; không âm thầm giảm coverage để chạy tiếp.
- Không thêm UI approval; backend caller bắt WorkloadPlanError và retry đúng
  approved_plan_id. Hard rejection không thể approval override.
- SCORE frequency/persistence/publication và 11 baseline failures ngoài scope.
- Không thêm runtime dependency; sklearn/numpy/scipy hiện có. OneMaster chỉ đọc,
  chưa xác nhận LICENSE; không copy source/import ngoài repo. Hash reference
  được ghi trong SPARSE_TFIDF.md.

## Commit / Next task — Sparse TF-IDF

Commit của task là commit chứa phần STATUS này (`git log -1` sau khi commit);
không tự tham chiếu hash gây vòng lặp. Next task: representative labeled shadow
evaluation và production budget/canary trước default enablement; chưa triển khai
các task SCORE/checkpoint/publication ngoài scope.

Task hiện tại (2026-10-08): thống nhất CandidateEngine giữa mọi dataset size.
Đọc [AGENT_RULES](AGENT_RULES.md); chính sách, config, telemetry, provenance và
giới hạn được ghi trong [CANDIDATES](CANDIDATES.md). Lịch sử input task giữ bên dưới.

## Completed — CandidateEngine

- Đọc docs/merge và code thực tế hai repo, xác minh roots/status/staged diff; chỉ sửa P-119. Giữ nguyên OneMaster dirty tree và mọi untracked/WIP có sẵn.
- Chung engine cho small `_compute`, compatibility `generate_candidates` và large StageRunner BUILD. Không còn đổi blocking policy tại 10.000 records.
- Giữ phone/email contact phụ VALID, government ID VALID, exact normalized name, cả name/address grams và bốn composite legacy; config transforms/limits dùng chung.
- Global index tích lũy mọi chunk trước khi expand; canonical snapshot ID pair, global union/dedup và toàn bộ sources. StageRunner không còn chọn một TF-IDF reason tùy ý.
- Deterministic sorted records/pairs/sources; snapshot copy khi ingest, duplicate snapshot ID fail rõ ràng. Giữ SHA-256 pair ID và serving blocking_reasons contract.
- Giữ normalized snapshot đã import thay vì normalize lại ở large path. Raw-only input vẫn dùng normalization trước BUILD. Empty snapshot có norm1 và candidate Parquet schema hợp lệ.
- Block guards trước expansion, global pair cap fail rõ ràng thay vì truncate; log generated/skipped observations và reasons, hashed block log, JSON metrics artifacts. Thiếu sklearn không được âm thầm bỏ TF-IDF.
- Equivalence/coverage so với frozen P-119 oracle đã chạy trước khi dùng engine chung mặc định theo yêu cầu task. Không thay TF-IDF implementation, scorer, schema, frontend hoặc serving persistence.

## Modified files — CandidateEngine

- `backend/src/masterone/mdm/pipeline/candidate_engine.py` (mới)
- `backend/src/masterone/mdm/pipeline/blocking.py`
- `backend/src/masterone/mdm/pipeline/stage_runner.py`
- `backend/src/masterone/mdm/resolution_service.py`
- `backend/tests/test_mdm/test_candidate_engine.py` (34 cases mới)
- `backend/tests/fixtures/legacy_candidate_reference.py` (frozen test-only oracle từ P-119 parent)
- `scripts/run_merge_baseline.py` (isolated candidates suite)
- `docs/merge/CANDIDATES.md` (mới), `docs/merge/PROJECT_MAP.md`, `docs/merge/STATUS.md`

## Tests — CandidateEngine

`.venv`, Python 3.11.9/Windows, source PYTHONPATH. Runner SQLite tạm,
R2/LLM/tracing tắt; không chạy app lifespan hoặc dịch vụ thật.

| Command/check | Kết quả thực thi | Report dưới artifacts/merge-baseline |
| --- | --- | --- |
| `.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py --suite all` trước sửa | 493 passed, 12 failed, 1 skipped; 39.40s; exit 1 | `20261008T101319Z-myzbt3q_/` |
| Cùng runner `--suite candidates`, final | 65 passed, 1 skipped; 3.55s; exit 0 | `20261008T102316Z-92d0z3w9/` |
| Cùng runner `--suite all`, final | 527 passed, 11 failed, 1 skipped; 38.42s; exit 1 | `20261008T102237Z-ibs339sg/` |
| Ruff: candidate_engine.py, blocking.py, test_candidate_engine.py, scripts/run_merge_baseline.py | All checks passed; exit 0 | Tool output |
| Ruff: stage_runner.py và resolution_service.py | Legacy style/unused DuckDB replacement-scan variables còn tồn tại; không tuyên bố whole-file lint PASS. Import block mới đã sắp xếp | Tool output |
| `git diff --check` | PASS | Tool output |

34 tests mới đều pass trong final full suite: cross-chunk phone; 10.001 records;
canonical multi-pass union; giữ nhiều TF-IDF sources qua Parquet/cand_blocks;
small/large cùng snapshot với batches 1/2/50.000; full StageRunner imported
snapshot vs `_compute`; permutation/ties determinism; contact quality;
composite config; oversized/pair budget telemetry; ID collision; snapshot
mutation; missing dependency; frozen legacy coverage ở guard boundaries.

**Full suite không PASS**: 11 failure identities là subset của baseline.
10 fixture StageRunner thiếu source_system/external_id, 1 development CORS
expectation trong APP_ENV=test. Fixture 1.000 records nay vào SCORE và lỗi
source_system trước CLUSTER external_id vì engine tìm thêm candidates như
small path. Empty snapshot baseline failure đã sửa trong phạm vi BUILD.
Existing TF-IDF `_partition_keys` skip vẫn giữ, không tính là PASS.

Exploratory candidates run `20261008T101837Z-y1chyjs2`: 56 passed/6 failed/1
skip do Arrow variable tên `table` là SQL keyword; đã sửa, rerun
`20261008T101858Z-ave9z3ko`: 62 passed/1 skip. Sau hardening, run
`20261008T102156Z-gxcihc07`: 64 passed/1 skip. Intermediate all
`20261008T101935Z-bb23adjt`: 524 passed/11 failed/1 skip. Reports ignored,
không commit. Không sửa assertions cũ hoặc suppress failures.

## Pending — CandidateEngine

- Global index, pair map và TF-IDF vẫn dùng RAM; chưa đo bounded RSS/throughput trên production. External index/spill và TF-IDF dense matrix là task riêng. Global pair cap không giới hạn RAM nội bộ TF-IDF trước union.
- Sorting ID làm deterministic TF-IDF ties; input legacy unsorted có thể chọn tied neighbors khác. Không thay threshold/partition/top-k hay âm thầm bỏ grams; coverage oracle kiểm canonical input order.
- SCORE corpus-frequency khác biệt, orchestration/persistence >10k, human decision replay và publication/checkpoint vẫn ngoài phạm vi. Candidate equivalence không chứng minh toàn bộ pipeline equivalence.
- 11 failures và 1 skip baseline nêu trên còn tồn tại; không kiểm định PostgreSQL/Neon/R2/LLM/frontend hoặc OneMaster tests.
- Input migration rehearsal và normalization WIP vẫn giữ pending theo lịch sử bên dưới.

## Dependencies — CandidateEngine

- Không thêm dependency. sklearn đã khai báo trong backend; nay thiếu dependency thì fail rõ ràng.
- OneMaster chỉ đọc làm tham chiếu design, SHA-256 working tree trong CANDIDATES.md; không runtime import hay phân phối source OneMaster.
- CandidateConfig là config dùng chung; limits/quy tắc cần được rollout cùng policy, không đặt theo size/chunk.

## Commit — CandidateEngine

Parent `86900b0dd6be8b820f91d7c5a5fdb4a9031d4b41`.
Message `feat(mdm): unify snapshot-wide candidate generation across dataset sizes`.
Hash báo final; chỉ stage đúng các file task, không stage artifacts/WIP.

## Next task — CandidateEngine

External/disk-backed index và bounded TF-IDF là bước riêng sau task này;
hoặc xử lý corpus-frequency equivalence nếu được giao. Không tự tích hợp
normalization WIP, production migration hoặc mở rộng refactor TF-IDF.

---

# Lịch sử: Input contract

Task trước (2026-10-08): kiểm tra lại input contract/schema resolver đã chuyển ở commit `7d18a29a5b17ccc712c196c3ba3ef3559585772b`, đối chiếu trực tiếp source OneMaster và giữ upload hiện tại. Baseline trước task được giữ trong [BASELINE](BASELINE.md); chi tiết contract mới ở [INPUT_CONTRACT](INPUT_CONTRACT.md).

## Completed

- Kiểm tra lại trực tiếp OneMaster `src/onemaster/schema.py` và `src/onemaster/io/preparation.py`; hai SHA-256 khớp INPUT_CONTRACT.md, quyền đọc hoạt động. Không sửa OneMaster.
- Audit 10 yêu cầu input: versioned aliases, strict duplicate/collision/ambiguity, string values, raw/normalized/provenance, lineage, snapshot UUID/source identity, uniqueness theo namespace, unknown fields và structured errors đều có implementation trong commit input trước.
- Bổ sung 6 test cases: trùng identity qua nhiều file có strip/case normalization và rollback; CSV multiline giữ logical row numbers, input lineage columns và raw zeros; ba required values rỗng có safe report; unknown contract version bị từ chối. Không thay runtime/frontend/candidate/scorer trong lượt kiểm tra lại.
- Chuyển phần normalization đang dở từ phiên trước sang worktree `artifacts/normalization-wip/` cùng backup `artifacts/normalization-wip.patch`; chưa commit/chưa hoàn tất, không thuộc kết quả input này. Main working tree không còn những sửa đổi normalization đó.
- Đọc rules/map/contracts/status/baseline, xác minh hai repo và staged/working status; chỉ sửa P-119, giữ untracked của người dùng và dirty tree OneMaster.
- Chụp SHA-256 schema.py/preparation.py tham chiếu trong INPUT_CONTRACT.md.
- Cấu hình `p119_input_v1` đóng gói cùng backend; resolver versioned với alias cũ/mới, header duplicate/Unicode collision/ambiguous aliases/required fields.
- Giữ original values, raw logical values, normalized data, mapping provenance; unknown fields lưu extra_fields và báo trong mapping report.
- Lineage chuẩn hóa; UUID snapshot riêng với stable `(source_system, external_id)`.
- Migration 0021 đổi uniqueness theo source, giữ legacy PK/JSON và metadata nullable. Rollback giữ raw/provenance columns; chặn overlapping source IDs không tương thích.
- Direct upload và chunk validation/commit dùng cùng resolver/identity. Giữ HTTP detail chuỗi và draft validation_error chuỗi cho UI, thêm structured error reports.
- Không sửa frontend, candidate generation hoặc scorer. Five-schema normalization equivalence, API upload cũ và migration preservation/rollback có tests.

## Modified files

Lượt kiểm tra lại chỉ sửa `backend/tests/test_mdm/test_input_contract.py`, `docs/merge/INPUT_CONTRACT.md` và `docs/merge/STATUS.md`. Danh sách dưới đây là implementation của commit input trước, giữ lại để bàn giao:

- `backend/src/masterone/mdm/input_contract_v1.json` (mới)
- `backend/src/masterone/mdm/input_schema.py` (mới)
- `backend/src/masterone/mdm/resolution_import.py`
- `backend/src/masterone/mdm/upload_sessions.py`
- `backend/src/masterone/mdm/models.py`
- `backend/src/masterone/mdm/migrate.py`
- `backend/src/masterone/main.py` (handler chỉ cho mapping errors)
- `backend/migrations/versions/0021_input_contract.py` (mới)
- `backend/pyproject.toml` (package JSON contract)
- `backend/tests/test_mdm/test_input_contract.py` (mới, 82 cases)
- `backend/tests/test_mdm/test_merge_baseline_contracts.py` (constraint assertion theo explicit migration 0021 được giao, không đổi assertion thuật toán)
- `scripts/run_merge_baseline.py` (thêm isolated input suite)
- `docs/merge/INPUT_CONTRACT.md` (mới)
- `docs/merge/CONTRACTS.md`
- `docs/merge/STATUS.md`

## Tests

Môi trường `.venv`, Python 3.11.9/Windows, source PYTHONPATH; isolated runner dùng SQLite tạm, tắt R2/LLM/tracing và không mở lifespan DB thật.

Lượt kiểm tra lại, command `.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py`:

| Suite/check | Kết quả thực thi | Report dưới artifacts/merge-baseline |
| --- | --- | --- |
| `--suite all`, trước thay đổi normalization/input review | 487 passed, 12 failed, 1 skipped; 38.39s; exit 1 | `20261008T095505Z-4jy3ev9_/` |
| `--suite input`, main sau tách normalization WIP, trước thêm tests | 124 passed; 14.56s; exit 0 | `20261008T100153Z-ip4g7l71/` |
| `--suite input`, final với 6 cases bổ sung | 130 passed; 15.92s; exit 0 | `20261008T100234Z-sh2tcys7/` |
| `--suite all`, final | 493 passed, 12 failed, 1 skipped; 37.93s; exit 1 | `20261008T100254Z-k1rkoqpm/` |
| `.venv/Scripts/python.exe -X utf8 -m ruff check backend/tests/test_mdm/test_input_contract.py` | All checks passed; exit 0 | Tool output |
| `git diff --check` | PASS, exit 0 | Tool output |

Full suite vẫn không PASS: cùng 12 failures/1 skip của baseline, không sửa assertion
hay suppress. Hai exploratory input runs khi normalization WIP còn trên main
(`20261008T100011Z-qvmzmoio/`, `20261008T100112Z-wc_tkljg/`) có 117 passed/7 failed;
đây là lỗi engine dở (5 fingerprint equivalence, 2 SQL ambiguous id), không dùng
làm kết quả validation input commit. WIP và notes đã giữ riêng, chưa tích hợp.

Kết quả implementation input trước (giữ để đối chiếu lịch sử):

| Command | Kết quả | Report dưới artifacts/merge-baseline |
| --- | --- | --- |
| `.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py --suite all` trước sửa | 405 passed, 12 failed, 1 skipped; 34.98s; exit 1 | `20261008T094113Z-jlyryoc2/` |
| Cùng runner `--suite input`, final | 124 passed; 14.93s; exit 0 | `20261008T094835Z-xfv3xprh/` |
| Cùng runner `--suite all`, final | 487 passed, 12 failed, 1 skipped; 36.05s; exit 1 | `20261008T094900Z-67ipyehl/` |

12 failure và 1 skip giống baseline trước sửa: 10 StageRunner fixture thiếu source_system/external_id, 1 empty norm1 path, 1 development CORS expectation trong APP_ENV=test. Không sửa/suppress failures ngoài task. Full suite không PASS.
Exploratory input run: 112 passed/1 failed do parse_upload còn kiểm unique ID cũ; đã sửa namespace check, input run kế tiếp 114 passed; final thêm compatibility tests.

Ruff trên Python files thay đổi và `git diff --check` được kiểm trước commit. Reports generated/ignored, không commit. Không chạy frontend/browser, OneMaster tests, PostgreSQL/Neon, R2 hoặc LLM thật.

## Pending

- Normalization engine là task riêng còn dở trong worktree nêu trên; có notes về lỗi census SQL và equivalence fingerprint, phải sửa/test trước khi đưa vào main. Không coi input tests là validation engine mới.
- Rehearsal migration PostgreSQL trong DB cách ly và triển khai/backups riêng; chưa auto-upgrade hoặc chạy service trên DB doanh nghiệp.
- Legacy original headers/whitespace/row positions không thể tái dựng từ JSON đã trim; metadata NULL được ghi rõ, không tạo lineage giả.
- 12 baseline failures và frontend Vitest setup vẫn thuộc task riêng.
- Candidate-path/frequency equivalence, NON_MATCH/REJECTED, worker ownership, checkpoint/bounded artifacts/publication và golden export regressions giữ backlog.

## Dependencies

- Không thêm runtime dependency; contract JSON khai báo setuptools package data.
- Source namespaces/required roles được versioned trong config; thêm source hoặc đổi mapping semantics cần version mới và compatibility tests.
- OneMaster working tree chỉ đọc; không runtime import ngoài P-119. Inventory không tìm thấy LICENSE/COPYING; không tuyên bố license/quyền phân phối source đó.
- PostgreSQL isolated integration environment cho deployment rehearsal.

## Commit

Lượt kiểm tra lại: parent `7d18a29a5b17ccc712c196c3ba3ef3559585772b`; message `test(import): revalidate OneMaster input contract and lineage`. Hash báo final. Không stage WIP/artifacts/untracked có sẵn.

Parent P-119: `f723f807803fbef35597ef04c42d48c6b0e4067a`.
Message task: `feat(import): add versioned lossless source schema contract`.
Hash chính xác báo final; tra bằng `git log -1 --format="%H %s" -- docs/merge/STATUS.md`.

## Next task

Đề xuất kiểm thử migration 0021 trên PostgreSQL cách ly với existing data/FKs và rollback/re-upgrade trước rollout. Nếu tiếp tục yêu cầu normalization ban đầu, dùng worktree WIP đã giữ và hoàn tất regression tests trước khi tích hợp. Baseline stabilization vẫn thuộc task riêng.
