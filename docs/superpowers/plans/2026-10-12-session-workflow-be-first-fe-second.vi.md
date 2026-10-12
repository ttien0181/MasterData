# Session Workflow BE-first / FE-second Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Thực hiện trực tiếp trong đúng **hai session độc lập**: Session 1 chỉ BE; Session 2 FE và tích hợp. Các checkpoint là bước trong session, không phải yêu cầu mở thêm session hoặc gửi thêm prompt. Không tự phân công subagent.

**Goal:** Chuyển trải nghiệm sau đăng nhập sang phiên có bốn tab, trì hoãn CLUSTER đến khi người dùng hoàn tất duyệt DECIDE, giữ nguyên thuật toán và policy của `feature/feature_5`.

**Architecture:** Dùng `feature/feature_5` làm nền; thêm lớp workflow, snapshot quyết định và lưu lựa chọn golden bên ngoài engine. Tái sử dụng chọn lọc giao diện `IamNTN`; engine hiện tại vẫn quyết định chuẩn hóa, candidate, score, eligibility, clustering, guard và survivorship. Schema/API được hoàn thành và kiểm chứng trong Session 1 trước khi Session 2 nối FE.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL/Neon, DB queue worker, Parquet/checkpoints/R2, Next.js/React/TypeScript, pytest/Vitest/Playwright. Dùng phiên bản đang cài và lockfile của dự án.

**Spec:** Phần “Thiết kế đã được người dùng duyệt” ngay trong file này. Người dùng đã duyệt luồng BE trong chat ngày 12/10/2026 và yêu cầu một file kế hoạch với hai prompt; tài liệu 11/10 chỉ là tham khảo.

**Ngày:** 12/10/2026, Asia/Bangkok. **Trạng thái:** Session 1 hoàn tất, BE-01..06 PASS. Session 2 / FE-01..06 chưa bắt đầu.

## Global Constraints

- Nền engine: `feature/feature_5`, baseline tham chiếu `2caf9459f97cdc62fe139dc2df01399b5c035475`. Nguồn FE: `IamNTN`, tham chiếu `2384d27864190c5095aa40538c9c0f22e2ed8237`. Khi thực thi ghi lại HEAD/diff thực tế; không tự reset workspace về các hash này.
- Giữ toàn bộ thay đổi có sẵn, staged/untracked và trạng thái xóa `docker-compose.qa.yml`; không checkout cả nhánh để chép FE, không `git add .`, không reset hay gom thay đổi người dùng vào commit.
- Không merge/cherry-pick nguyên nhánh hoặc commit lớn của `IamNTN`. Đọc bằng `git show IamNTN:<path>`, chuyển theo module/dependency cần thiết.
- **Không tạo bất kỳ Docker Compose mới nào**, kể cả override, test, QA hoặc tên biến thể. Sửa `docker-compose.yml` hiện có. Nếu không còn file Compose nào, chỉ tạo sau khi có quyền rõ ràng từ người dùng; không tự tái tạo file đã xóa.
- Runtime/kiểm chứng tích hợp dùng **`.env.qa`**; không thay bằng `.env`, không in secrets, URL có credential, signed URL hoặc toàn bộ `docker compose config` đã nội suy.
- Không đổi ngưỡng, trọng số, luật Group/SubGroup, eligibility, source policy, hard constraints, bridge checks, union ordering, source priority hoặc survivorship. Không tự bật engine-v2/streaming/Splink/sparse bằng workflow flag.
- Không mang nhánh `manual_components` của `IamNTN` sang để bỏ qua guard. Không thay clustering bằng `IamNTN/workflow_clusters.cluster_memberships()` hoặc survivorship bằng `field_registry.build_fields()` của nhánh đó.
- Giữ engine selection được pin theo run; API, spawned worker, retry và golden phải cùng profile. Bổ sung workflow không đổi cấu hình đã pin của phiên cũ.
- GET, chuyển tab, refresh, mở phiên cũ, upload, start-decide và review từng cặp không được ngầm chạy CLUSTER. Chỉ lệnh **finalize-decisions** hợp lệ được enqueue CLUSTER; không auto-finalize từ polling hoặc khi duyệt cặp cuối.
- Giữ auth/ownership/CSRF/Origin/capabilities, revision, lease/heartbeat/fencing, audit, retry/idempotency và lịch sử bất biến. Giữ policy Group D hiện có; không quyết định lại mô hình phê duyệt trong phạm vi này.
- Chỉ sửa luồng sau đăng nhập. Không thêm SSO, connector, định dạng mới, sharing, thuật toán mới, benchmark nhiều triệu dòng hay quy trình production deployment.
- Test đơn vị dùng DB/store tạm và LLM mock; đây là cô lập test, không thay runtime QA. Smoke tích hợp dùng tài nguyên `.env.qa`, dữ liệu synthetic và namespace riêng; không xóa dữ liệu cũ hoặc tắt LLM để tuyên bố đã kiểm chứng live.
- Hai session được phép làm toàn bộ phạm vi tương ứng. Không yêu cầu người dùng duyệt từng checkpoint; hỏi chỉ khi thiếu thông tin thực sự cần thiết hoặc vượt phạm vi đã duyệt.

## Review Focus

| Điều kiện | Hành vi bắt buộc | Checkpoint/test sở hữu |
|---|---|---|
| Full/serial/streaming/resume và endpoint review cũ cùng tồn tại | Phiên workflow-v2 không có đường tắt chạy CLUSTER trước finalize | BE-03 `test_all_v2_paths_stop_before_cluster` |
| A–B, B–C được chọn nhưng A–C bị guard/veto chặn | Giữ guard nền, không công bố tập cluster cuối sai | BE-04 `test_bridge_conflict_preserves_baseline_guard` |
| Hai tab finalize hoặc sửa pair/golden cùng lúc; worker cũ hết lease | Một operation, stale 409, worker cũ không publish | BE-02/04/05; FE-04 |
| Có singleton, không có candidate, thiếu chứng cứ hoặc trường ngoài schema | Không mất bản ghi, không bịa NON_MATCH/luật auto mới; trạng thái rõ | BE-03/04/05; FE-03/04 |
| Phiên cũ, browser mới, retry/reopen sau golden thủ công | Giữ lịch sử, choices server và nguồn hợp lệ, không rerun khi đọc | BE-05/06; FE-02/04/05 |

---

## 1. Thiết kế đã được người dùng duyệt

### 1.1 Luồng nghiệp vụ

1. Upload/commit → PREPARE: lưu input, dùng chuẩn hóa hiện có, tạo profile dữ liệu; chưa tạo cluster/golden.
2. Lệnh start-decide → BUILD_CANDIDATES → SCORE → DECIDE: lưu kết quả cặp, Group/SubGroup và evidence rồi dừng.
3. Review từng cặp hoặc auto/bulk theo eligibility hiện có: chỉ lưu quyết định, chưa tạo proposal/cluster/golden.
4. Người dùng bấm **“Hoàn tất phân loại và tạo nhóm”** → kiểm tra completion → đóng băng quyết định → enqueue CLUSTER → chạy engine hiện có.
5. Tạo golden dự thảo theo luật nền, người dùng chọn trường hoặc auto chọn và xác nhận; lưu BE.
6. Chốt phiên khi mọi golden hiện hành đã được xác nhận; kết quả cuối được xem/xuất theo snapshot.

Cluster hiện có dựng golden nội bộ có thể tiếp tục làm vậy **sau finalize-decisions** để giữ nguyên thuật toán; output này là dự thảo, không phải xác nhận của người dùng. Không cần tách thuật toán ra chỉ vì FE có hai tab.

Một phiên tương ứng một `ResolutionRun`. Group/SubGroup là phân loại **cặp**, không phải cluster. Các bản ghi hợp lệ không thuộc proposal nhiều thành viên được biểu diễn singleton ở lớp publication; việc này không thay thuật toán union.

### 1.2 Trạng thái và phiên lịch sử

Giữ `ResolutionRun.status` cho compute compatibility. Bổ sung `workflow_version`, `workflow_state`, `workflow_revision`, `decision_revision`, `cluster_revision`, `golden_revision` (revision bắt đầu 0, tăng khi mutation liên quan thành công).

| Trạng thái | Lệnh/kết quả chuyển tiếp |
|---|---|
| `DATA_PREPARING` | PREPARE thành công → `DATA_READY` |
| `DATA_READY` | start-decide → `DECIDE_RUNNING` |
| `DECIDE_RUNNING` | DECIDE publish đầy đủ → `DECIDE_REVIEW` |
| `DECIDE_REVIEW` | finalize-decisions hợp lệ → `CLUSTER_QUEUED` |
| `CLUSTER_QUEUED` | worker claim → `CLUSTER_RUNNING` |
| `CLUSTER_RUNNING` | publish hợp lệ → `GOLDEN_REVIEW`; xung đột → `DECIDE_REVIEW` kèm blocker |
| `GOLDEN_REVIEW` | finalize-golden hợp lệ → `FINALIZED` |
| `FINALIZED` | Xem/xuất; reopen có chủ đích → `DECIDE_REVIEW` |
| `LEGACY_READ_ONLY` | Adapter đọc phiên workflow-v1; không tự chuyển phiên cũ sang v2 |

Failure lưu trên operation: `FAILED`/`RETRYABLE_FAILED`, stage/error_code; giữ checkpoint cuối, không xóa choices/input. Khi queued/running cluster, mutation quyết định trả 409. Reopen bị chặn khi có job đang chạy; tăng revision, vô hiệu hóa kết quả hiện hành cho xuất mới, giữ snapshot lịch sử.

Để Session 1 không phá FE cũ: migration mặc định dữ liệu cũ là v1; API tạo/commit phiên nhận `workflow_version: 1 | 2`, mặc định 1 cho caller cũ. FE mới Session 2 gửi 2. `SESSION_WORKFLOW_ENABLED` chỉ cho phép tạo/thực thi v2, không đổi engine selection. Legacy routes của v1 giữ nguyên; routes cũ trên run-v2 phải guard trước side effect để không bypass workflow. Không quảng bá FE cũ là giao diện hoàn chỉnh cho v2.

### 1.3 Quyết định và bảo toàn thuật toán

- API review giữ thao tác `merge`, `no_merge`, bổ sung `defer` cho workflow; lưu theo semantics hiện có `ACCEPTED`, `REJECTED`, `DEFERRED`. Không remap `REJECTED` thành `NON_MATCH` hoặc tự tạo cannot-link. Explicit negative edge giữ riêng theo hợp đồng nền.
- Lưu baseline DECIDE và review overlay riêng. `primary_group`, `subgroup`, score, evidence gốc không bị rewrite thành quyết định người dùng.
- Completion xét output DECIDE đã seal, các review bắt buộc còn pending/deferred, bulk đang hoạt động và conflict chưa giải quyết. Auto/non-match/blocked được xử lý theo policy hiện có; không ép người dùng click mọi candidate, không auto approve cặp thiếu chứng cứ.
- Thuật toán nền hiện lọc một số `INSUFFICIENT_EVIDENCE`: giữ nguyên quyết định/đầu ra clustering của engine; có thể giữ payload đầy đủ ở artifact/DTO để FE thống kê và xem. Không nâng thiếu chứng cứ thành merge hoặc NON_MATCH; nếu tạo review blocker phải có cách xử lý `no_merge` đúng semantics nền.
- Adapter accepted/rejected phục vụ CLUSTER phải tái sử dụng evaluator, guard và logic manual-review nền. Có thể tách cơ học hàm dùng chung khỏi side effect tạo proposal sớm; không viết union-find/guard mới. Auto path không override phải tương đương nền; manual path đối chiếu hành vi nền với cùng quyết định hợp lệ. Nếu guard từ chối lựa chọn người dùng, trả conflict rõ ràng.
- CLUSTER có thể bổ sung triage/conflict mới: lưu riêng với baseline DECIDE bất biến. LLM annotation hiện gắn với baseline sau cluster phải được kiểm tra lại dependency; explanation trước cluster không được tuyên bố đã có bằng chứng cluster. Giữ gateway, ngân sách và policy hiện có.

### 1.4 API đích cho bàn giao FE

Prefix mới: `/api/v1/mdm/runs/{run_id}/workflow`. Tất cả endpoint theo ownership/capability của dự án, GET thuần đọc. Endpoint list dùng paging server, `limit` 1..100; DTO list `{items,total,next_cursor}`, cursor là ID cuối trang. Response workflow có `allowed_actions`, `blockers`, `active_operation`, `retryable_operation`, revision và counters có đơn vị. Tạo trực tiếp: multipart `POST /api/v1/mdm/runs`, form `workflow_version=2`. Resumable upload dùng API cũ, commit bằng `POST /api/v1/mdm/upload-sessions/{session_id}/commit?workflow_version=2` (query, không phải JSON body); version được lưu trước khi spawned import. Cả hai mặc định v1 khi caller không gửi version.

| Method/suffix | Body chính / response |
|---|---|
| GET `/` | `WorkflowRead`: run/version/state/revisions/counts/actions/blockers/active + retryable operation |
| GET `/data` | File/schema/profile, raw/normalized sample có paging, lỗi và chất lượng thực tế |
| POST `/start-decide` | `{request_id,expected_workflow_revision}` → `OperationRead`, HTTP 202 |
| GET `/pairs` | Filter group/subgroup/resolution; baseline + overlay + eligibility |
| POST `/pairs/{pair_id}/decision` | `{request_id,expected_workflow_revision,expected_pair_revision,decision}` → pair và revisions mới |
| POST `/bulk-decisions` | `{request_id,expected_workflow_revision,expected_decision_revision,decision,scope}`; scope `{pair_ids}` hoặc `{filter,excluded_pair_ids}` → operation 202; không gửi expected_pair_revision |
| POST `/finalize-decisions` | `{request_id,expected_workflow_revision,expected_decision_revision}` → CLUSTER operation 202 |
| GET `/clusters` và `/clusters/{cluster_id}` | Chỉ generation hiện hành; list paging cluster. Detail có `members` paging bằng limit/cursor, `evidence` và `pair_evidence` paging riêng bằng evidence_limit 1..100 / evidence_cursor offset ≥0; llm_stats + workflow_context bound vào snapshot |
| GET `/clusters/{cluster_id}/golden` | Draft/status/revision, field values và alternatives/provenance |
| PUT `/clusters/{cluster_id}/golden` | `{request_id,expected_workflow_revision,expected_cluster_revision,expected_golden_revision,selections}` → `{items:[GoldenRead],workflow_revision,golden_revision}` |
| POST `/golden/auto-select` | Revisions như PUT; `cluster_ids`, `reset_manual:false` mặc định; dùng luật nền |
| POST `/clusters/{cluster_id}/golden/confirm` | Revisions như PUT, không có selections |
| GET `/golden` | Golden hiện hành có trạng thái draft/confirmed, paging |
| POST `/finalize-golden` | `{request_id,expected_workflow_revision,expected_golden_revision}` → trạng thái FINALIZED |
| POST `/reopen-decisions` | `{request_id,expected_workflow_revision,reason}` → DECIDE_REVIEW |
| GET `/operations/{operation_id}` | Chỉ operation thuộc run; progress/status/error_code |
| POST `/operations/{operation_id}/retry` | `{request_id,expected_workflow_revision}` → cùng operation/snapshot, 202; chỉ async operation mới nhất bị FAILED/RETRYABLE_FAILED/CANCELLED ở state hợp lệ |

`selections`: object theo tên trường; mỗi trường `{selection_mode: "SOURCE_CHOICE" | "MANUAL_OVERRIDE", source_record_id?: string, value?: JSONValue}`. Source choice phải là thành viên và value thật của cluster hiện hành; có thể bỏ value để server lấy. Override cho 6 trường nền name/phone/email/address/dob/government_id: string; phone/email còn nhận list[string]. Extra fields chỉ SOURCE_CHOICE từ dữ liệu thật, auto_rule_available=false; không có ranking mới. Phone/email giữ multivalue và observations; chọn primary không xóa các observation nền. Auto/confirm trả cùng envelope items như PUT. SAVE merge selections theo field; auto reset_manual=true xóa các lựa chọn trên 6 trường nền, extra-source choices vẫn giữ.

Request replay cùng request_id/body trả cùng kết quả; request_id khác payload hoặc stale revision trả 409. Replay kiểm tra trước stale-check. Mutations pair/bulk/golden/reopen có audit. Snapshot decision lưu identity input/config/policy và revisions/hash; seal artifact trước, sau đó transaction ngắn kiểm tra revision và atomically cập nhật state + snapshot reference + enqueue. Không giữ DB transaction dài trong compute/upload R2.

Xuất tiếp tục dùng luồng preview/execute/download/history hiện có với adapter v2: chỉ FINALIZED/revision hiện hành, không dùng choices từ FE làm nguồn chính thức. Singleton, multivalue và giá trị thủ công phải khớp golden server. Phiên không có bản ghi hợp lệ trả trạng thái rỗng/blocker rõ ràng, không finalize giả.

### 1.5 FE đích

- `/app`: Tổng quan, upload, draft upload và lịch sử; không tự chuyển sang phiên gần nhất.
- `/app/uploads/[uploadSessionId]`: upload/resume/validate/commit; sau commit mở data của run-v2.
- `/app/sessions/[runId]/data`, `/decisions`, `/clusters`, `/golden`: header Tổng quan phiên và đúng bốn tab.
- Tab chưa sẵn sàng hiển thị lý do. Cluster queued/running chỉ hiện tiến độ; không hiện cluster cuối trước publish. Golden tab chỉ có kết quả cuối khi FINALIZED; draft có nhãn rõ.
- Mở cluster để chọn field hoặc auto theo luật nền; lưu/confirm BE. LocalStorage chỉ có thể giữ nháp UI, không làm nguồn chính thức cho export.
- Giữ light/dark, responsive, auth, quyền; tiện ích audit/history/split/policy hiện có nằm ngoài bốn bước, chỉ hiện theo quyền/readiness.
- Legacy run mở adapter/legacy workspace, không tự chạy pipeline. URL quyết định điều hướng và hỗ trợ reload/Back/Forward.

## 2. Cách ghi checkpoint và tiếp tục công việc

Mỗi bước bên dưới là checkbox. **Sau khi hoàn tất từng bước, cập nhật checkbox và ghi một dòng evidence vào nhật ký cuối file.** Sau khi đạt tiêu chí một checkpoint, cập nhật hàng tương ứng sang PASS và ghi HEAD/diff, tests/exit code, artefact hoặc lỗi. Không đánh PASS bằng suy luận hoặc dựa trên kết quả của `IamNTN`.

Trạng thái bảng: `NOT_STARTED`, `IN_PROGRESS`, `PASS`, `FAIL`, `BLOCKED`. Checkbox chỉ đánh `[x]` khi có kết quả kiểm chứng. Nếu hết session giữa chừng, giữ trạng thái thật, ghi bước tiếp theo; session sau đọc file và tiếp tục phần chưa đạt, không chạy lại các bước đã có bằng chứng còn hợp lệ. Session 2 chỉ làm FE khi BE-06 PASS.

| Checkpoint | Session | Deliverable | Trạng thái | Evidence |
|---|---|---|---|---|
| BE-01 | 1 | Baseline, phạm vi engine và QA wiring | PASS | pytest config+preservation 2 passed exit 0; docker compose --env-file .env.qa -f docker-compose.yml config --quiet exit 0; daemon permission checked separately |
| BE-02 | 1 | Schema/state machine/queue/API contract | PASS | workflow contract + additive migration: 5 passed exit 0; single head 0028_session_workflow; older migration fixture uses latest ORM on 0018 and needs reflected inserts |
| BE-03 | 1 | PREPARE/DECIDE tách riêng, pair/bulk không cluster | PASS | stages+finalize+canonical preservation 9 passed exit 0; streaming/resume/direct DECIDE spies all zero cluster calls |
| BE-04 | 1 | Finalize snapshot và guarded CLUSTER | PASS | finalize+stages+canonical baseline 9 passed exit 0; further concurrency/equivalence checks scheduled BE-06 |
| BE-05 | 1 | Golden server, finalize/export/reopen/legacy | PASS | golden integration RED to GREEN: 1 passed exit 0; source/manual/auto/confirm/finalize/export/reopen/history covered |
| BE-06 | 1 | Kiểm chứng BE và bàn giao API cho FE | PASS | All BE gates PASS: workflow30, baseline232, compatibility83, LLM58, preservation2, PostgreSQL4 checks, QA HTTP/worker/R2 smoke4 groups, source/runtime/OpenAPI handoff check, Ruff and diff-check exit0. FE NOT_STARTED; stop at BE-06; no commit/push/production deploy. |
| FE-01 | 2 | Chuyển chọn lọc component và API client | PASS | Selected shell/theme CSS and adapted workflow components compile; lint exit0, typed client3 GREEN and workflow8 GREEN; no dependency versions changed |
| FE-02 | 2 | Tổng quan/upload/navigation/bốn tab | PASS | Unit upload12 and workflow10 GREEN; run-change/unmount abort initial/poll reads; deterministic3 PASS URL/BackForward, hub no restore, singleton/gates; full Vitest71 PASS; v1 deep-link adapter preserves explicit query and routes v2 away |
| FE-03 | 2 | Data và DECIDE/Group/SubGroup/bulk/finalize | PASS | Unit68 passed including zero-candidate, insufficient GroupD permission, pending gates; deterministic3 PASS covers no implicit finalize and singleton display |
| FE-04 | 2 | Cluster/golden editor/server choices | PASS | Final live QA1 PASS run4db76fbb: source/manual/extra choices survive reload and fresh authenticated context, Auto preserves manual; real stale409 read/review and explicit second confirm succeeded. Full workflow tests GREEN and browser acceptance16 PASS; no official localStorage choices. |
| FE-05 | 2 | Golden cuối/export/history/legacy/responsive | PASS | Browser acceptance16 PASS including keyboard/error-read retry, mobile Golden multivalue/light-dark/no overflow and export button hit-test above dock. Final real QA desktop/mobile screenshots visually reviewed; source provenance readable cards, no raw JSON table. Native history CSV byte-equal export. |
| FE-06 | 2 | Browser tích hợp QA và nghiệm thu | PASS | Section6.2 final handoff completed with routes/client contracts, .env.qa reproduction commands, live run/revisions/hash/coverage, tests72+16+1 and preservation1, real stale review, legacy0 tasks and transport/LLM/volume limits. All acceptance checklist checked; no commit/push/production deploy. |

## 3. Session 1 — sửa BE trước

### BE-01 — Khóa baseline và cấu hình QA

**Files đọc:** `docs/merge/{ENGINE_ADAPTER,DECISIONS,CLUSTERING,GENERATIONS,CHECKPOINTS,WORKERS,STREAMING}.md`, `backend/src/masterone/{config.py,mdm/engine_adapter.py}`, `.env.qa` (không log nội dung), `docker-compose.yml`.

**Files sửa/tạo:** config loader hiện có; `docker-compose.yml`; `backend/tests/test_mdm/test_workflow_engine_preservation.py`, `test_workflow_qa_config.py`; baseline JSON/hash dưới `artifacts/session-workflow-2sessions/<campaign>/` (không secrets).

- [x] Ghi git status/HEAD, nguồn IamNTN, diff có sẵn, danh sách engine modules bảo vệ và engine flags hiện hành đã redacted.
- [x] Chạy fixtures chuẩn hóa/candidate/score/decision/cluster/golden nền; lưu output canonical gồm membership, selected values, alternatives, ordering, guard reasons. Dùng cùng fixture IDs/profile; chỉ loại UUID attempt/run khác và thời gian kỹ thuật khỏi so sánh.
- [x] Viết và chạy test RED cho lựa chọn env QA/cùng DB API-worker, engine flags không bị workflow đổi; thêm regression fixtures source-priority khác verified-priority, shared contact, bridge, veto, manual choices và singleton.
- [x] Thêm loader `MASTERONE_ENV_FILE` với fallback `.env` trong `get_settings()`; chạy local bằng `MASTERONE_ENV_FILE=.env.qa` và `SESSION_WORKFLOW_ENABLED=true`. Setting workflow mặc định false cho caller cũ; QA runtime bật true. Compose dùng `env_file: .env.qa` cho API/worker, đặt workflow gate true cho cả hai và bỏ override ép local DB; không đổi engine flags. Không copy secret vào YAML hoặc mount env vào image.
- [x] Chạy test config và baseline bảo toàn; kiểm tra Compose cấu trúc bằng `docker compose --env-file .env.qa -f docker-compose.yml config --quiet`. Ghi PASS khi đạt, không chạy full config có secrets.

**Gate:** baseline có thể replay độc lập; config QA dùng đúng profile, không vô tình bật thuật toán khác. Không chạy engine baseline với LLM thật lặp lại để tạo expected.

### BE-02 — Schema, trạng thái, queue và DTO

**Files:** tạo `backend/src/masterone/mdm/workflow_models.py`, `workflow_contracts.py`, `workflow_service.py`, `backend/src/masterone/api/mdm_workflow.py`; sửa `mdm/models.py`, `job_queue.py`, `resolution_worker.py`, `config.py`, `main.py`, tạo migration additive trong `backend/migrations/versions/` nối đúng Alembic head thực tế; test `test_workflow_contract.py`, `test_workflow_migrations.py`.

**Interfaces:** `get_workflow(db, run_id: str, actor) -> WorkflowRead`; `begin_operation(db, run_id: str, operation_type: str, request_id: str, payload: dict, expected_workflow_revision: int, actor) -> OperationRead`. Request/response theo 1.4; operation types tối thiểu `PREPARE`, `DECIDE`, `BULK_DECISIONS`, `CLUSTER`.

- [x] Viết/chạy RED: valid/invalid state transitions, workflow-v1 vẫn đọc, stale 409, request replay, ownership/capabilities, migration không đổi pair/override/history cũ.
- [x] Tạo schema additive: workflow columns, operations/idempotency, decision snapshots, cluster generations/members, golden draft/selections và conflict records. Unique `(run_id,request_id)` cho operation; unique record membership trong generation. Import model để Alembic/worker thấy metadata.
- [x] Nối queue dispatch bằng task operation reference/type; giữ unique task/run nếu đang dùng, cùng lease/fencing/attempt history; không tạo hàng đợi song song hoặc reset job sống. Claim lấy operation từ DB và kiểm tra type/identity.
- [x] Mount API GET workflow/operation và guards cho routes cũ trên v2. Tạo/commit phiên nhận workflow_version theo 1.2; chưa thay FE.
- [x] Chạy GREEN và migration graph/current-head trên DB test; ghi contract OpenAPI thực và PASS checkpoint.

**Gate:** transaction enqueue/state/idempotency có kiểm chứng; legacy caller còn hoạt động. Migration QA chỉ dùng target `.env.qa` đã xác minh là QA, không startup vô tình migrate `.env`.

### BE-03 — Điểm dừng DECIDE, review pair và bulk

**Files:** sửa `mdm/resolution_service.py`, `pipeline/stage_runner.py`, các checkpoint/streaming entry point chỉ khi cần phân pha; `mdm/upload_sessions.py`, `resolution_import.py`, `api/mdm_uploads.py`, `api/mdm_resolution.py`; tạo `mdm/workflow_jobs.py`, `workflow_review.py`, `workflow_bulk.py`, `data_profile.py`, `decision_publication.py`; test `test_workflow_stages.py`, `test_workflow_review.py`, `test_workflow_bulk.py`.

**Interfaces:** `StageRunner.run_prepare(...)` trả prepared checkpoint/profile; `StageRunner.run_decide(...)` trả baseline pairs/artifact/counters và không proposals; giữ `run_all(...)` cho v1. `execute_workflow(operation_id: str, factory, progress) -> None`; `completion_blockers(db, run_id: str) -> list[dict]`. `decide_pair(db, run_id, pair_id, request, actor)` và `run_bulk_decisions(operation_id, factory, progress)` chỉ ghi overlay.

- [x] Viết/chạy RED `test_all_v2_paths_stop_before_cluster`: spy call CLUSTER/build_proposals/build_golden, upload/PREPARE/DECIDE/review/GET đều có call count 0; phủ serial, streaming, resume, direct runner và route review cũ.
- [x] Tách pha orchestration cơ học; PREPARE dùng chuẩn hóa nền và seal checkpoint để DECIDE dùng lại, không chuẩn hóa lại theo luật khác. Giữ serial/parallel payload, candidate census, score/config identity và global ordering.
- [x] Publish baseline DECIDE bền vững trước `DECIDE_REVIEW`. Giữ payload thiếu chứng cứ ở lớp hiển thị nếu cần; stats rõ và không âm thầm biến chúng thành merge. Trường bị CLUSTER bổ sung chưa được dựng giả.
- [x] Chuyển review-v2 sang overlay, không chạy side effect tạo proposal/golden của `decide_review_pair()` cũ. Auto/bulk xét eligibility server từng cặp; Group/SubGroup/filter chỉ là scope, không lệnh union toàn nhóm. Có progress và counts accepted/skipped/blocked.
- [x] Hoàn thiện `/data`, `/pairs`, mutation/bulk và blockers. Kiểm thử 0 candidate, singleton, insufficient evidence, không có valid record, pending/defer, bulk concurrent, revision và policy Group D nền.
- [x] Chạy GREEN cùng score-parallel/streaming/checkpoint regression liên quan; ghi PASS sau từng bước và checkpoint.

**Gate:** workflow dừng thật ở DECIDE_REVIEW, DB chưa có membership/golden của v2; engine score/decision tương đương baseline. Retry không dùng FULL checkpoint để bỏ qua gate.

### BE-04 — Finalize và CLUSTER từ snapshot bất biến

**Files:** tạo `mdm/workflow_finalize.py`, `workflow_cluster_reads.py`, `cluster_publication.py`; sửa `workflow_jobs.py`, `workflow_service.py`, `api/mdm_workflow.py`, publication adapter hiện có; nếu cần tách hàm manual-review dùng chung khỏi `resolution_pairs.py`/`resolution_items.py` thì không đổi evaluator/guard; test `test_workflow_finalize.py`, `test_workflow_clusters.py` và preservation.

**Interfaces:** `finalize_decisions(db, run_id: str, request, actor) -> OperationRead`; `run_cluster(operation_id: str, factory, progress) -> None`; adapter gọi `pipeline.clustering.build_proposals(...)` và logic review nền, không cài cluster engine thay thế.

- [x] Viết/chạy RED: finalize trước completion bị 409; hai request cùng revision chỉ một enqueue; snapshot không đổi khi worker chạy; stale writer/expired owner không publish; missing/corrupt artifact không success.
- [x] Seal decision snapshot với input/config/policy identity + baseline/overlay + revision/hash; recheck completion/revision trong transaction ngắn rồi atomically enqueue/state/snapshot reference. Replay không nhân đôi job.
- [x] Triển khai adapter accepted/rejected bằng logic nền và gọi clustering nền. Giữ weight/record-ID ordering, bridge/cannot-link/reviewer-veto/source checks, resource-budget semantics. Không bỏ guard hoặc thay budget failure thành thành công.
- [x] Thêm singleton cho bản ghi hợp lệ còn lại ở publication. Validate mỗi record đúng một membership; không trùng/mất record. Guard conflict mới lưu riêng, quay lại DECIDE_REVIEW và chặn kết quả cuối; không nới thuật toán để hợp ý reviewer.
- [x] Publish generation/reference/state atomically dưới fence. GET clusters trước sẵn sàng chỉ trả readiness/409 theo contract, không compute. Kết quả cluster có golden nội bộ được giữ như draft.
- [x] Chạy GREEN, `test_bridge_conflict_preserves_baseline_guard`, ordering permutations, auto/manual equivalence, singleton coverage và worker retry; ghi PASS.

**Gate:** không override thì cluster membership/guard/golden base khớp baseline; manual choices hợp lệ được áp dụng đúng semantics nền, lựa chọn vi phạm được báo conflict.

### BE-05 — Golden server, export, reopen và legacy

**Files:** tạo `mdm/golden_service.py`; sửa `api/mdm_workflow.py`, `mdm/resolution_preview.py`, `export_stream.py`, `resolution_split.py` và `api/mdm_resolution.py` chỉ ở boundary cần nối workflow; test `test_workflow_golden.py`, `test_workflow_exports.py`, `test_workflow_legacy.py`.

**Interfaces:** `build_golden_drafts(db, run_id: str, cluster_revision: int) -> None` dùng `pipeline.golden.build_golden()` trong engine_context đã pin; `update_golden`, `auto_select`, `confirm_golden`, `finalize_golden`, `reopen_decisions` có request/actor và revisions theo 1.4.

- [x] Viết/chạy RED: ranking source-first nền, multivalue/alternatives/provenance, source không thuộc cluster bị từ chối, manual choice survive reload/retry, stale revision 409, unconfirmed không final/export.
- [x] Persist field selections/draft/confirmation BE. Auto-select dùng luật nền; mặc định không reset manual. Extra fields chưa có luật auto giữ manual/read-only policy rõ. Không dùng field_registry ranking của IamNTN.
- [x] Finalize-golden kiểm tra đủ draft/confirmation/coverage và revision hiện hành; lưu result hash. Preview/export lấy server choices và singleton, giữ snapshot/download lịch sử; không nhận browser choices làm dữ liệu chính thức.
- [x] Reopen giữ audit/snapshot, invalidates readiness của cluster/golden hiện hành; thay membership chỉ carry source choice còn hợp lệ, đánh dấu cần review nếu không hợp lệ. Split/legacy mutation trên v2 phải đi qua revision/gate, không bypass.
- [x] Adapter v1 chỉ đọc khi mở qua workflow, old v1 APIs vẫn đúng; không giả review/finalize mới và không rerun khi GET. Giữ export cũ và stored engine selection.
- [x] Chạy GREEN và regressions golden/export/legacy/retry; ghi PASS.

**Gate:** cùng input/profile, auto golden giống nền; choices server tồn tại qua browser mới; export đúng kết quả đã chốt, không ghi đè snapshot lịch sử.

### BE-06 — Kiểm chứng và bàn giao bắt buộc

**Files:** test preservation/integration đã tạo; cập nhật phần 6 ngay trong file này. Session 1 không sửa component/route FE.

- [x] Chạy nhóm workflow mới và baseline regression theo lệnh mục 5; ghi test counts/exit codes, baseline failure và skip riêng. Lỗi liên quan thay đổi phải sửa, không tắt check để xanh.
- [x] Chạy PostgreSQL concurrency/fencing/migration checks trên QA, dưới test namespace; SQLite xanh không thay bằng chứng PostgreSQL. Không truncate DB QA toàn cục.
- [x] Chạy API + worker cùng `.env.qa` qua Compose hiện có; thực hiện synthetic API path upload→prepare→decide→review→finalize→cluster→golden→export. Ghi run/operation/revision/artifact IDs redacted; chứng minh worker thực thi và GET không enqueue.
- [x] Đối chiếu protected engine diff và canonical baseline. Source module core giữ nguyên hoặc chỉ mechanical extraction có parity test; mọi thay đổi semantics làm checkpoint FAIL. LLM lỗi do ngân sách/output giới hạn ghi đúng, không sửa policy/đổi model chỉ để xanh.
- [x] Điền bàn giao BE: route/DTO thực, migrations, runtime command/env selection, readiness/blockers/error codes, legacy behavior, test evidence và source hash/diff. Nếu contract phải thay tên vì code thực tế, cập nhật bảng 1.4 trước khi bàn giao, giữ semantics đã duyệt.
- [x] Đánh BE-06 PASS chỉ khi gate bắt buộc đạt. Kết thúc Session 1 bằng link file và tóm tắt; không bắt đầu FE trong session này.

**Gate sang FE:** BE-01..06 PASS; workflow API sử dụng được, preservation và QA API smoke có evidence. Nếu BLOCKED không bịa PASS để Session 2 tiếp tục.

## 4. Session 2 — FE và tích hợp sau BE

### FE-01 — Đọc bàn giao và chuyển component có chọn lọc

**Files:** `frontend/AGENTS.md`, guides trong `frontend/node_modules/next/dist/docs/`, `frontend/src/lib/{api,contracts}.ts`; tạo/adapt `workflowApi.ts`, `workflowContracts.ts`, `workflowNavigation.ts`; component IamNTN cần thiết và CSS liên quan. Không thay package versions/Dockerfile chỉ để giống IamNTN.

- [x] Xác minh BE-06 PASS và API/OpenAPI thực tế; đọc HEAD/diff mới, bảo vệ thay đổi Session 1 và người dùng.
- [x] Lập dependency list từ `git show IamNTN:<path>`; chọn `AppSession`, `SessionHub`, `SessionWorkspace`, `SessionTabs`, `UploadWorkspace`, `DataOverview`, `DecisionWorkspace`, `ClusterWorkspace`, `GoldenFieldEditor`, `GoldenRecords` cùng helper/CSS cần dùng. Không mang auto-finalize/cluster-repair policy mới sang.
- [x] Viết/chạy RED contract/client/navigation tests; build typed clients khớp bảng API BE bàn giao, CSRF/cookie/error/revisions theo pattern hiện có. FE không tự suy eligibility từ score/Group.
- [x] Chuyển component theo dependency, giữ shared shell/theme; chạy TypeScript/client tests GREEN và đánh PASS.

### FE-02 — Tổng quan, upload, workspace và bốn tab

**Files:** `frontend/app/app/{layout.tsx,page.tsx}`, tạo `uploads/[uploadSessionId]/page.tsx`, `sessions/[runId]/layout.tsx` và bốn `page.tsx`; adapter `legacy/page.tsx` nếu cần; `SessionHub`, `SessionWorkspace`, `SessionTabs`, `UploadWorkspace`, `ResolutionWorkspace`.

- [x] Viết/chạy RED: đăng nhập đến Tổng quan upload/history, không auto-open phiên cuối; mở history/GET không POST xử lý; bốn tab có gate, URL reload/Back/Forward đúng run.
- [x] Nối upload/resume/validate/commit hiện có, gửi workflow_version=2, sau commit mở data; profile PREPARE có tiến độ và không start-decide ngầm.
- [x] Mount shell bốn tab; readiness/allowed_actions đọc BE, queued/running hiện progress, lỗi có retry read. Phiên v1 mở legacy, không tự migrate.
- [x] Chạy navigation/upload recovery tests GREEN; kiểm tra chuyển phiên bỏ selection cũ và cancel request poll đúng; đánh PASS.

### FE-03 — Data và duyệt DECIDE

**Files:** `DataOverview`, `DecisionWorkspace`, `PairCategoryOverview`, `PairComparison`, `PairDecisionDialog`, `GroupAcceptDialog`, `SelectionControls`, `usePageSelection`, `useWorkflowAction` và CSS/tests tương ứng, chỉ lấy dependency cần thiết.

- [x] Viết/chạy RED: raw/normalized/quality và units đúng; Group/SubGroup là pair; auto scope server; pending/defer/bulk chặn finalize; chuyển tab không compute.
- [x] Data hiển thị input/profile/lỗi/paging và nút start-decide. Bảng pair dùng evidence/score units thật, baseline và review riêng, filters/search/paging.
- [x] Nối manual/auto/bulk, counts accepted/skipped/blocked; xử lý stale 409 bằng reload và giữ ý định người dùng, không retry mutation với revision tự thay âm thầm.
- [x] Nút “Hoàn tất phân loại và tạo nhóm” chỉ gửi finalize-decisions khi BE cho phép; show blockers nếu không. Sau submit chuyển cluster progress; không gọi finalize từ useEffect/polling/cặp cuối.
- [x] Chạy tests GREEN gồm singleton/0-candidate/insufficient và Group D theo policy; đánh PASS.

### FE-04 — Cluster detail và golden editor

**Files:** `ClusterWorkspace`, `GoldenFieldEditor`, helper presentation/selection và tests; không mặc định chuyển module GoldenResolutionPanel/repair từ IamNTN nếu kéo theo thuật toán ngoài phạm vi.

- [x] Viết/chạy RED: cluster chưa ready không có kết quả cuối; membership/singleton đúng; source/auto/save/confirm gọi BE; reload lấy choices server; stale revision 409 và multivalue không mất dữ liệu.
- [x] Cluster list/detail phân trang, hiển thị member/source/evidence. Conflict trở lại DECIDE qua blocker, không tự gộp lại hoặc bỏ guard.
- [x] Field editor hiển thị selected value, alternatives, nguồn và lý do từ engine. Auto theo luật nền, preserve manual mặc định; save/confirm rõ ràng, quyền theo BE.
- [x] Khi revision/membership đổi reload dữ liệu server và hiển thị lựa chọn cần xác nhận lại; localStorage không ghi đè lựa chọn chính thức. Extra fields không có auto rule ghi rõ trạng thái.
- [x] Chạy tests GREEN; browser reload/máy phiên mới xác minh choices tồn tại; đánh PASS.

### FE-05 — Golden cuối, export, lịch sử và tiện ích

**Files:** `GoldenRecords`, adapter `ResolutionFlow`, `SessionUtilities` nếu cần, export/history/audit components hiện có và tests.

- [x] Viết/chạy RED: draft không mang nhãn kết quả cuối, unconfirmed không finalize/export, FINALIZED dùng server choices; legacy history không rerun, quyền và light/dark không regress.
- [x] Golden tab hiển thị draft/confirmed/final rõ, nút chốt phiên và blockers; preview/export/download lấy snapshot BE, singleton và multivalue đúng.
- [x] Nối tiện ích history/audit/split/policy theo API readiness/capabilities; không biến tiện ích thành bước thứ năm. Giữ source/user override/provenance khi xem lịch sử.
- [x] Kiểm tra desktop/mobile, light/dark, tab/keyboard, loading/empty/error, action không bị dock che; chạy tests GREEN và đánh PASS.

### FE-06 — Nghiệm thu browser QA

**Files:** tạo `frontend/e2e/session-workflow.spec.ts`, `frontend/e2e/session-workflow-qa.spec.ts`, tests component/client; sửa `frontend/playwright.config.ts` nếu cần để có browser path hợp lệ ở máy chạy, không mặc định sao chép config IamNTN; cập nhật phần 6.

- [x] Chạy lint/Vitest/build và browser acceptance deterministic; ghi rõ mocked tests không thay QA integration.
- [x] Dùng FE/API/worker cùng QA runtime từ Session 1. Browser synthetic path: login→Tổng quan→upload→data→DECIDE→manual/bulk→explicit finalize→cluster→source/auto golden→confirm→finalize→preview/export→reload/history.
- [x] Browser ghi bằng chứng trước explicit finalize không có cluster operation/membership/golden; GET/tab/reload/history không tạo job; sau finalize có đúng một job. Reload hoặc browser context mới khôi phục selections từ server.
- [x] Đối chiếu CSV/export với golden BE, membership/singleton coverage, guards/conflict/stale và legacy access. Chạy lại BE tests bị ảnh hưởng nếu có sửa tích hợp.
- [x] Ghi ảnh/trace/log redacted, command/exit code và source diff; đánh FE-06 PASS chỉ khi required checks đạt. Không tự đánh production-ready hoặc deploy/push.
- [x] Hoàn thiện bảng nghiệm thu và bàn giao trong file; kết thúc Session 2 với kết quả thực, giới hạn và checkpoint chưa đạt nếu có.

Session 2 được sửa lỗi BE nhỏ phát hiện khi tích hợp trong phạm vi contract đã duyệt, kèm regression và cập nhật bàn giao; không đổi thuật toán/luật hoặc bắt đầu subsystem mới. Nếu BE-06 chưa PASS, ưu tiên hoàn tất gate BE trước và báo rõ phạm vi FE còn lại, không bỏ gate để giữ hình thức “hai session”.

## 5. Lệnh kiểm chứng và phạm vi bằng chứng

Chạy PowerShell ở root; `.venv/Scripts/python.exe` tồn tại tại thời điểm lập kế hoạch. Commands cho test mới chạy sau khi files đã được tạo. Test fixtures phải isolate DB/store/provider trước import/startup; không chạy suite phá dữ liệu trên Neon QA.

```powershell
# Backend: test workflow mới và bảo toàn engine, cùng lint các file đã sửa.
$env:PYTHONPATH = 'backend/src'
  # Unit tests: đường dẫn không tồn tại để không nạp .env/.env.qa của developer.
  # PowerShell xóa env var nếu gán ''; vì vậy không dùng chuỗi rỗng để isolate.
  $env:MASTERONE_ENV_FILE = 'artifacts/session-workflow-2sessions/20261012/nonexistent-unit-env'
.\.venv\Scripts\python.exe -m pytest backend/tests/test_mdm -k workflow -q
.\.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_decision_semantics.py backend/tests/test_mdm/test_cluster_guards.py backend/tests/test_mdm/test_safe_clustering.py backend/tests/test_mdm/test_cluster_determinism.py backend/tests/test_mdm/test_stage_runner.py backend/tests/test_mdm/test_score_parallel.py backend/tests/test_mdm/test_streaming.py backend/tests/test_mdm/test_checkpoint_resume.py backend/tests/test_mdm/test_generation_staging.py backend/tests/test_mdm/test_golden_and_benchmark.py backend/tests/test_mdm/test_upload_sessions.py -q

# Runtime QA local: loader này được thêm trong BE-01; chưa tồn tại trước đó.
$env:MASTERONE_ENV_FILE = '.env.qa'
$env:SESSION_WORKFLOW_ENABLED = 'true'
# API và worker phải được launch với cùng env; dùng entry point hiện có.

# Compose hiện có: chỉ validate, không in nội dung đã nội suy.
docker compose --env-file .env.qa -f docker-compose.yml config --quiet
```

Trong `frontend/`:

```powershell
npm run lint
npm run test
npm run build
npm run test:e2e -- e2e/session-workflow.spec.ts
npm run test:e2e -- e2e/session-workflow-qa.spec.ts
```

- API smoke QA phải là test riêng với test owner/run IDs và cleanup giới hạn tài nguyên do test tạo; không dùng pytest fixture truncate database.
- Ruff: chạy `.venv\Scripts\python.exe -m ruff check <danh sách file BE đã sửa>` và lưu danh sách thật trong evidence; migration graph theo cấu hình Alembic của repo, ghi lệnh đã dùng.
- Ghi số passed/failed/skipped và lý do skip; không coi skip/mock là live PASS. Có failure baseline ngoài phạm vi thì ghi baseline reproduction, không sửa thuật toán để chữa test.
- Bảo toàn engine: compare normalized values, candidate identities/reasons, score units/evidence/decisions, group/subgroup, canonical edge order, guard reasons, membership và golden/provenance/alternatives cho cùng selected profile. Counter UI toàn bộ candidate có thể rộng hơn output legacy do giữ insufficient payload; không dùng khác biệt DTO này để che khác biệt engine.
- Hash artifact/checkpoint phải phân pha và dependency identity; không replay artifact FULL để tạo proposal sớm. LLM outputs không deterministic được kiểm chứng contract/binding/policy riêng, không so golden bằng text AI.
- Không yêu cầu benchmark 100k, chứng nhận production, hosting/staging mới hoặc tăng ngân sách LLM cho hai session này. Giới hạn QA/provider thật ghi nguyên trạng.

## 6. Bàn giao và nhật ký dùng chung cho hai session

### 6.1 Bàn giao Session 1 — hoàn tất BE, sẵn sàng cho session FE

**Source và phạm vi:** branch `feature/feature_5`, HEAD nền `2caf9459f97cdc62fe139dc2df01399b5c035475`; source vẫn là working diff, chưa commit/push. Không checkout/merge IamNTN. Không sửa frontend. Giữ mọi dirty/untracked từ lúc bắt đầu, kể cả `docker-compose.qa.yml` đang bị xóa. Không tạo Compose mới. Chỉ dùng `docker-compose.yml` hiện có. Session 2 phải giữ working diff này.

Evidence campaign: `artifacts/session-workflow-2sessions/20261012/`. `source.json` là baseline trước sửa; `source-final.json` liệt kê file/hash và protected diff; `backend-source.patch` chứa tracked patch + file BE/script mới, không chứa .env, password hoặc signed URL. `baseline.json`/`baseline.sha256` giữ nguyên bản capture trước sửa. Cùng bytes được đưa vào `backend/tests/test_mdm/fixtures/workflow_baseline.{json,sha256}` để preservation test chạy độc lập checkout, không phụ thuộc artifact ignored. StageRunner là module pipeline duy nhất đổi hash: thêm entry PREPARE/DECIDE, chặn full runner cho v2, chỉnh lint cơ học; candidate/scoring/decision/guard/clustering/golden/semantic/checkpoint/streaming core giữ hash. `resolution_service._compute` dùng điểm dừng sau DECIDE; manual adapter gọi evaluator/guard/golden_payload nền và shared reviewed_members, không có union-find hay source ranking mới.

**Schema và vận hành:** head duy nhất `0028_session_workflow` sau `0027_llm_policy`. Additive columns trên run/upload-session/task và 7 bảng mdm_workflow: operations, artifacts, pairs, generations, clusters, members, conflicts. Run cũ mặc định v1/LEGACY_READ_ONLY, revisions 0; serving/history cũ không rewrite. Unique run/request_id, run/generation-revision và generation/record-member. Downgrade giữ dữ liệu workflow; re-upgrade kiểm tra cột đã tồn tại. Có supplied Alembic connection để kiểm tra schema QA riêng (Neon pooler không nhận startup options search_path); runtime bình thường vẫn dùng config hiện có.

```powershell
$env:PYTHONPATH = 'backend/src'
$env:MASTERONE_ENV_FILE = '.env.qa'
$env:SESSION_WORKFLOW_ENABLED = 'true'
# Chỉ QA local, không production. API và spawned worker nhận cùng env_file .env.qa.
docker compose --env-file .env.qa -f docker-compose.yml config --quiet
docker compose --env-file .env.qa -f docker-compose.yml -p p119-session-qa up -d --build backend worker
# Migration explicit nếu cần ở QA; development/test startup cũng kiểm tra head hiện tại.
.\.venv\Scripts\python.exe -m masterone.mdm.migrate
# Chạy kiểm chứng live riêng; scripts tự chọn .env.qa, không truncate shared DB.
.\.venv\Scripts\python.exe scripts/verify_session_workflow_postgres.py
.\.venv\Scripts\python.exe scripts/verify_session_workflow_qa.py
```

API base `http://localhost:8001/api/v1`, health `http://localhost:8001/health`, OpenAPI `http://localhost:8001/openapi.json`. QA allowed Origin `http://localhost:13000` / `http://127.0.0.1:13000`; đây là URL FE dành cho Session 2, FE chưa được launch/sửa trong Session 1. Cookie auth + header X-CSRF-Token từ login + đúng Origin cho mọi mutation. Project Compose `p119-session-qa` có backend/worker/database; DB mà backend/worker thực sự dùng là QA Neon (profile neon), không phải đổi sang local database service để làm xanh test. SESSION_WORKFLOW_ENABLED=true; mdm_engine_v2_enabled=false, decision_semantics_v2_enabled=false, cluster_safety_v2_enabled=false, generation_staging_enabled=false, mọi streaming/resume flag false như source baseline. Profile được lưu trong ResolutionEngineSelection lúc PREPARE và dùng lại ở API/worker/retry/CLUSTER. llm_enabled=true và semantic_address_enabled=true ở QA; không đổi model/budget/provider flags.

**DTO FE cần dùng:** bảng 1.4 là tên route/body đã thực thi. OpenAPI request models trong `workflow_contracts.py`; response đọc/sync chi tiết ở `mdm_workflow.py` và services. `WorkflowRead.counts` có records/candidates/pairs/insufficient, mỗi counter `{value,unit}`. PairRead `{pair_id,baseline,decision,revision,eligibility:{manual_allowed,auto_allowed,blocked_reasons},score_unit:'p119_rule_v1'}`. Score/evidence giữ đơn vị và payload nền; Group/SubGroup nằm trong baseline, overlay không rewrite baseline. Filter resolution dùng UNDECIDED/ACCEPTED/REJECTED/DEFERRED; group/subgroup dùng giá trị baseline, scope chỉ chọn cặp. OperationRead `{operation_id,run_id,operation_type,status,stage,progress,error_code,result}`. Async PREPARE/DECIDE/BULK_DECISIONS/CLUSTER dùng ResolutionTask + attempts/lease hiện có; poll GET operation/workflow, không POST từ polling. Bulk progress `{accepted,skipped,blocked,unit:'pairs'}`.

GoldenRead `{cluster_id,cluster_revision,golden_revision,revision,status,fields,alternatives,selections}`. fields là golden effective trên server (value/provenance/source và values cho phone/email); alternatives có source_record_id/source_system/value/auto_rule_available. PUT/auto/confirm trả items envelope; expected_golden_revision là revision toàn run, không dùng revision từng cluster thay thế. Sau mỗi mutation đọc revisions trả về hoặc GET workflow; 409 thì reload và giữ ý định user, không tự thay revision rồi gửi lại. Confirm mọi cluster, bao gồm singleton. finalize-golden trả state/revisions/result_hash; export/preview chỉ dùng snapshot hash hiện hành.

**Gates và error handling:** invalid version/body/limit trả 422; 404 cho run không thuộc owner, operation/cluster/pair không thuộc run. 403 cho capability/CSRF/Origin. 409 detail có code: SESSION_WORKFLOW_DISABLED, LEGACY_READ_ONLY/USE_WORKFLOW_API, RUN_ARCHIVED, ACTIVE_OPERATION, STALE_REVISION (revision/current), STALE_PAIR_REVISION, REQUEST_ID_PAYLOAD_MISMATCH, WORKFLOW_INVALID_STATE, DECISIONS_LOCKED, PAIR_MERGE_BLOCKED, DECISIONS_INCOMPLETE (blockers), CLUSTERS_NOT_READY, GOLDEN_LOCKED, GOLDEN_CONFIRMATIONS_REQUIRED, REOPEN_NOT_ALLOWED, OPERATION_NOT_RETRYABLE, WORKFLOW_NOT_FINALIZED, STALE_EXPORT_REVISION. Snapshot/input/config/hash/coverage lỗi đều chặn publish. Completion blockers gồm DECIDE_NOT_READY, ACTIVE_OPERATION, NO_VALID_RECORDS, PENDING_DECISIONS với count/unit pairs, CLUSTER_CONFLICT. GOLDEN_REVIEW trả UNCONFIRMED_GOLDEN count/unit clusters và chỉ hiện finalize-golden khi đủ confirmation. Action list đã lọc quyền ở API.

Retry explicit qua route ở bảng 1.4, cùng operation/snapshot; lease recovery tự động dùng attempt mới và fences cũ. Cancel dùng `POST /api/v1/mdm/runs/{run_id}/cancel` hiện có; cancel queued retire operation để retry được. GET workflow có retryable_operation cho async operation mới nhất còn hợp lệ; không retry historical failed compute ở state FINALIZED. Reopen có reason/audit, tăng revisions, chặn export mới đến khi finalize lại, giữ historical export download. Choices còn nguồn hợp lệ được carry sang generation sau và luôn phải confirm lại; collision không tự chọn người thắng.

**Export/legacy:** giữ routes `/runs/{run_id}/export-readiness`, `/previews`, `/previews/{preview_id}/execute`, `/exports/{export_id}/download`, history/audit hiện có. Preview body `{choices:{},acknowledge_unreviewed:false}`; execute `{execute_request_id}` tối đa 80 ký tự; execute response là CSV, header X-Export-Id. v2 từ chối choices browser không rỗng với 422 SERVER_GOLDEN_CHOICES_REQUIRED. CSV là schema export nền, singleton + multivalue + server override; upload R2 và đọc lại checksum/size trước publish. Download có thể 302 signed URL: browser follow redirect; test dùng client mới cho R2, không forward cookie/CSRF. v1 giữ các legacy routes/serving/history cũ; adapter workflow của v1 chỉ đọc. Old computation/review/golden/split/policy mutation trên v2 trả USE_WORKFLOW_API trước side effect; GET cũ vẫn có thể đọc historical serving và không thay current workflow. Split/policy utilities chưa có v2 mutation riêng; FE ẩn bằng readiness, không gọi mutation legacy để bypass.

**Verification đã đạt:** workflow 30 passed (793 deselected), required baseline 232 passed, compatibility job_queue/RBAC/resolution_items/session_contract/qa_regressions + retry 83 passed, LLM gateway/policy/publication/semantic/explanation/endpoints 58 passed; exit 0, không skip. Ruff mọi file Python BE sửa/tạo PASS; danh sách trong ruff-files.json. PostgreSQL live: additive migration bảo toàn v1, hai tab một enqueue/409, expired worker không publish, hai golden writer một thành công/409; schema riêng đã cleanup, không truncate QA. Test migration lịch sử dùng SQL cột lịch sử thay ORM mới ở schema 0018/0021. Test cluster/evidence pagination và guards có trong workflow_regressions; một số test gộp ở regressions/finalize/golden thay vì tách đúng từng filename dự kiến trong plan. Logs và commands cụ thể ở mục 5/nhật ký; baseline failure ở seed cũ đã sửa, không sửa thuật toán.

**Live smoke cuối PASS, exit 0:** `qa-smoke.json/log` ghi 4 nhóm kiểm tra upload/prepare, DECIDE/review/bulk/GET không cluster, finalize race chỉ một CLUSTER + singleton coverage, server golden qua fresh login + preview/CSV/R2 download. Worker có 4 attempt SUCCEEDED trên cùng worker `82d48bcbef8a:030c3549-ff6a-440f-bd5a-1e32c4871ecd`. Bulk accepted=1/skipped=2/blocked=0, unit pairs. 3 records được cover đúng một lần, một cluster 2 members và một singleton. CSV có “QA Server Choice”, checksum/size đã kiểm chứng R2, download bằng signed redirect khớp bytes export. Repeated GET trước finalize không tạo generation hay CLUSTER operation. Hai tab dùng request_id khác nhau/cùng revision nhận 202 và 409; chỉ một CLUSTER operation, hai immutable DECISION_SNAPSHOT artifact cùng hash có thể được seal trước cạnh tranh, artifact thua không được publish.

| Evidence QA | ID / giá trị thật |
|---|---|
| Synthetic owner | `81b2924d-4f5f-4b16-bc28-0198104e8371`; `session-workflow-d06ddaed74d64abf8fd4b75d35db2638@example.com` |
| Run | `3afab16d-37a8-476d-a8ff-9642876eed26` |
| PREPARE operation / artifact | `d022b2ad-442b-46e1-a570-96a452f7cfbc` / `0680d3b1-84d7-43b1-898b-a4eb55c7ecdd` |
| DECIDE operation / artifact | `8c21a68a-bb6c-4411-b86f-225abe277aa1` / `a8981de1-9ae3-48ed-a7f9-31b93a14f97c` |
| CLUSTER operation / decision snapshot | `109dfa0b-35d4-4621-9b85-87ada44b626d` / `91ac4f89-f211-482a-ba39-9c4c4348cbf3` |
| CLUSTER result artifact | `5a067631-3e4e-4e30-b5be-5eb31dc589c0`; hash `f22ae85339bee0f8d027641536397a9e32e5cd88a7ab0f733738721411961772` |
| Export | `7b68c4ac-030a-4af8-93c9-b09c2a458c21` |
| Final state / revisions | FINALIZED; workflow=14, decision=3, cluster=1, golden=5 |
| Final result hash | `23e9fcf89a60e249cbdeee0613b661f875f346a913b23dedef9a9354a45ebdc0` |

`handoff-check.json/log` PASS: API/worker cùng source SHA và QA DB fingerprint, runtime khớp local source, state/revisions đã đọc lại từ QA, OpenAPI thực có 17 workflow paths được lưu ở `workflow-openapi.json`. `runtime-api.json`/`runtime-worker.json` chỉ lưu hash/profile/flags, không lưu DSN plaintext. PostgreSQL test schema `session_workflow_qa_c6394650538b47b1be1d1c93762aae7e` đã cleanup. Các lần smoke trước bị rebuild ngắt kết nối đã ghi FAIL trong nhật ký, không tính là live PASS; `qa-smoke-interrupted.json` giữ một lần tái hiện. Synthetic owners/runs mới được giữ để tra evidence; password/session-token không log/artifact. FE browser smoke cần tài khoản QA riêng đủ quyền hoặc tạo owner synthetic mới và cấp STEWARD chỉ owner đó; không có password dùng lại từ artifact.

**Giới hạn đã biết:** không chạy FE/browser/100k benchmark/production deployment. Operation snapshot dùng JSON seal trong DB; chưa có chứng nhận scale lớn cho artifact/cluster member-id arrays/alternatives. List/member/evidence paging đã có nhưng payload artifact nội bộ vẫn tải theo generation. CSV giữ bộ cột nền, extra-source choices có trong golden/snapshot nhưng không thêm cột CSV hay rule ranking. Collision choice sau reopen bị bỏ và unconfirmed, FE phải review lại; chưa có DTO reason riêng cho từng collision. LLM unit checks dùng provider giả lập; live QA giữ provider thật, số selected/outbound/errors được báo đúng từ CLUSTER artifact, không suy PASS provider từ zero-outbound.

**Điều kiện FE bắt đầu:** BE-01..06 PASS, contract và smoke cuối đã có evidence ở trên. Session 1 dừng tại đây; FE-01..06 vẫn NOT_STARTED, chưa có browser/FE acceptance.

### 6.2 Bàn giao Session 2 — hoàn tất FE và browser QA

- Giữ checkout `feature/feature_5`, HEAD `2caf9459f97cdc62fe139dc2df01399b5c035475`; không commit/push. BE-06 được xác minh lại bằng `scripts/verify_workflow_handoff.py`: API/worker/source/DB/OpenAPI PASS. `scripts/capture_frontend_workflow_handoff.py` kiểm tra 46 file BE vẫn khớp manifest Session 1; không sửa BE trong Session 2.
- Source FE gồm 47 file, cả file mới/untracked, trong `artifacts/session-workflow-2sessions/20261012-fe/frontend-source.patch` và manifest SHA256 `frontend-source.json`. Tham chiếu IamNTN lấy bằng `git show`, dependency ledger `donor-dependencies.json`; adapt shell/hub/tabs/upload/data/decision/cluster/golden, dùng CSS `workflow.css` và `DecisionWorkspace.module.css`, giữ shared `ShellTopBar`/`ThemeToggle`/`SessionsHub`/`ResolutionIntake`. Không mang cache choices, field ranking, preview cluster/repair hay auto-finalize của donor.
- `/app` là hub; query cũ `?run=&view=` là đường dẫn explicit qua `SessionEntry`, không tự mở phiên cuối. `/app/uploads/[uploadSessionId]` hỗ trợ tạo mới/resume. `/app/sessions/[runId]/{data,decisions,clusters,golden}` là bốn tab. `/utilities` là history/audit/chính sách đọc; v2 split/simulate hiển thị chưa hỗ trợ. `/app/legacy?run=` chỉ mở v1; v2 chuyển về data.
- Client v2 tại `workflowApi.ts`/`workflowContracts.ts`, dùng cookie/CSRF hiện có và UUID request/revision BE. Commit đúng prefix `/upload-sessions/{session_id}/commit?workflow_version=2`; default caller v1 giữ nguyên. GET/poll/tab/reload không mutate. Eligibility lấy BE; bulk có page/filter/exclusions, revision và counters theo đơn vị cặp. Golden hiển thị multivalue/provenance/source/manual/extra source-only, save/auto/confirm explicit; stale giữ nháp, đọc lại server và không tự rebase/retry mutation. Choices chính thức không lưu localStorage.
- Required checks PASS, exit 0: lint; Vitest **72 tests / 13 files**; build; deterministic browser **16 tests** (6 workflow, 10 legacy/theme); browser QA thật **1 test / 6,6 phút**; engine preservation **1 test**. Logs và exit-code files trong `20261012-fe`; `check-exit-codes.json` tổng hợp kết quả cuối. Mocked browser tests không thay thế QA integration. Test baseline legacy mất kết nối được sửa để phát lỗi WebSocket đúng implementation; mock queue lỗi cũ không còn làm nhiễm test toast. Review tại checkout theo chỉ dẫn không tự phân công subagent đã kiểm tra contracts/revisions, chống double-click, thứ tự GET, cleanup polling, capability, legacy gate và CSV transport.
- Runtime dùng `.env.qa`, Compose hiện có `p119-session-qa`, API `localhost:8001`, FE `localhost:13000`; không tạo/khôi phục Compose. Docker Desktop đã bị dừng giữa các lần điều hướng hội thoại; runner QA khởi động lại đúng project và ghi exit code riêng. QA dùng `SESSION_WORKFLOW_QA=1`, browser Edge đã cài; config QA dùng build/start, default deterministic development vẫn dùng dev.
- Lỗi tích hợp transport: browser PUT trực tiếp kho lưu trữ báo `net::ERR_FAILED`; FE sau 3 lỗi network chuyển cùng part/checksum sang proxy API đã có. RED/GREEN regression + 12 upload tests PASS; permanent checksum rejection không fallback. Không đổi BE/storage config/engine. Chưa kết luận nguyên nhân hạ tầng chính xác của direct upload.
- Tải CSV lịch sử dùng browser navigation tới GET có auth/ownership của BE; browser theo redirect signed storage, thay vì fetch cross-origin bị kẹt. Regression RED → GREEN; QA tải file lịch sử thành công và byte-equal CSV vừa xuất. Quyền vẫn do endpoint BE kiểm tra. Golden cuối dùng cards có nhãn giá trị/multivalue/provenance và nguồn mở rộng, đọc được trên mobile; padding cuối trang và browser hit-test xác nhận nút xuất không bị dock che. Keyboard, light/dark, lỗi đọc/thử lại, URL cũ và v2 legacy guard đều PASS.
- QA PASS: run **`4db76fbb-e9b5-4c63-8868-ec418f6090da`**, cuối `FINALIZED`, workflow revision **14**, cluster revision **1**, golden revision **5**. Trước explicit finalize: **0** CLUSTER operation/generation/membership/cluster; sau đó đúng **1** CLUSTER operation/generation, **2** cluster gồm singleton, **3** memberships bao phủ toàn bộ input. Browser chỉ POST finalize-decisions **1 lần**. Bulk accepted **1**, skipped **2**, blocked **0**, unit **pairs**. Source/manual/extra choices tồn tại sau reload và context đăng nhập mới; Auto giữ manual. `browser-qa.json` và `browser-proof.json` lưu bằng chứng.
- QA gặp stale thật khi reload trong lúc Auto chưa hoàn tất: request workflow revision **10**, server **11**, trả **409 STALE_REVISION**. FE đọc lại và giữ lựa chọn; test đối chiếu thông báo rồi thực hiện lần xác nhận thứ hai bằng click explicit, không có replay tự động. Legacy run **`d15c206f-a7fd-4935-afec-581b48622d98`** chỉ có browser GET; kiểm tra DB thêm xác nhận **0 ResolutionTask**, không rerun.
- CSV **2 dòng / 562 bytes**, đối chiếu giá trị golden server và coverage **3 bản ghi**; lịch sử byte-equal. Snapshot result hash **`64c362d09c4a04752e6dad14801f23388020637bb94a15cae8df83c7941d0dc6`**; CSV SHA256 **`c4909f4ea405e60b4f9cea34e50089705f73bcbca03e70782d20fb3044594bd9`**. `browser-export.csv`, `browser-history.csv`, `qa-final-desktop.png`, `qa-final-mobile-dark.png` là artifact synthetic. Ảnh desktop/mobile đã được xem để kiểm tra bố cục.
- Không ghi trace authenticated vì có cookie/CSRF/signed URL. `browser-network-redacted.json` chỉ method/path/status, nhãn object-storage/error code hoặc revision của 409; credentials sinh trong pipe/in-memory, không lưu log/artifact. Traces của mocked acceptance chỉ dùng token giả không hợp lệ. Artifacts nằm trong thư mục ignored; source/tests/scripts và file kế hoạch nằm trong checkout để review, chưa commit/push.
- FE-01..FE-06 và checklist nghiệm thu hoàn tất. Giới hạn: chưa kiểm chứng live provider LLM hay large-volume/performance; direct object-store upload vẫn cần fallback trong QA. Split/simulate v2 hiển thị chưa hỗ trợ theo readiness đã bàn giao. Fixture/account/export synthetic giữ lại trong QA làm evidence. Không tuyên bố production-ready, không push/deploy production.

Lệnh tái kiểm chứng từ repo root (Docker Desktop đang chạy; dùng Compose hiện có):

```powershell
$env:PYTHONPATH = (Resolve-Path 'backend/src').Path
$env:PYTHONUTF8 = '1'
docker compose --env-file .env.qa -p p119-session-qa up -d --no-deps backend worker
.venv/Scripts/python.exe scripts/verify_workflow_handoff.py
.venv/Scripts/python.exe scripts/capture_frontend_workflow_handoff.py
Push-Location frontend
$env:NEXT_PUBLIC_API_BASE_URL = 'http://localhost:8001'
npm.cmd run lint
npm.cmd test
npm.cmd run build
$env:SESSION_WORKFLOW_QA = '1'
node.exe node_modules/@playwright/test/cli.js test e2e/session-workflow-qa.spec.ts --workers=1
node.exe node_modules/@playwright/test/cli.js test --grep-invert 'live QA upload' --workers=1
Pop-Location
```

QA CLI tự chạy FE tại port 13000 bằng production build; muốn mở UI riêng: tại `frontend`, chạy `npm.cmd run start -- --hostname 127.0.0.1 --port 13000`. Next hiện có cảnh báo `output: standalone` khi dùng `next start`; runner thực tế vẫn khởi động và các kiểm tra trên PASS. Không đổi package/Dockerfile để che cảnh báo.

### 6.3 Nghiệm thu cuối

- [x] Sau đăng nhập là Tổng quan upload + lịch sử; mở phiên cũ không rerun.
- [x] Một phiên có đúng bốn tab; URL/reload/Back/Forward đúng.
- [x] PREPARE và DECIDE dùng thuật toán nền, chưa chạy cluster/golden.
- [x] Pair/Group/SubGroup/auto/bulk là review quyết định, chưa tạo cluster.
- [x] Chỉ explicit finalize hợp lệ enqueue CLUSTER; guard nền còn nguyên.
- [x] Cluster/singleton bao phủ toàn bộ bản ghi hợp lệ; conflict không bị che.
- [x] Golden auto dùng luật nền; source/manual choices lưu server qua reload.
- [x] Chốt/xuất chỉ khi đủ confirmation/revision; CSV đúng golden server.
- [x] Legacy/auth/ownership/CSRF/capability/audit/history/retry không regress.
- [x] API và worker thực sự dùng `.env.qa`; không có Compose mới.
- [x] Engine preservation và required BE/FE/browser checks có evidence thật.

### 6.4 Nhật ký sau mỗi bước

Thêm một dòng sau mỗi checkbox được xử lý, kể cả FAIL/BLOCKED. Mẫu: `thời gian | checkpoint/bước | kết quả | file/operation | command + exit/test count | evidence | bước tiếp`.

| Thời gian Asia/Bangkok | Checkpoint/bước | Kết quả | Evidence và bước tiếp |
|---|---|---|---|
| 2026-10-12 | Lập kế hoạch | PLAN_ONLY | Chưa sửa product code; mọi checkpoint thực thi NOT_STARTED |

| 2026-10-12 00:30 | BE-01/1 | PASS | HEAD 2caf945; IamNTN 2384d27; dirty/untracked preserved; protected hashes/source flags artifacts/session-workflow-2sessions/20261012/source.json |

| 2026-10-12 00:30 | BE-01/2 | PASS | Canonical baseline captured before product edits: serial pipeline, bridge, veto, cannot-link, golden source/verified/manual and singleton; baseline.json + SHA256 |

| 2026-10-12 00:30 | BE-01/3 | PASS | RED config 1 failed: MASTERONE_ENV_FILE ignored; database values redacted. Existing guard/shared-contact fixtures retained. |

| 2026-10-12 00:31 | BE-01/4 | PASS | get_settings MASTERONE_ENV_FILE fallback .env; workflow default false; existing Compose API-worker use .env.qa and workflow true; local DB overrides removed |

| 2026-10-12 00:31 | BE-01/5 | PASS | pytest config+preservation 2 passed exit 0; docker compose --env-file .env.qa -f docker-compose.yml config --quiet exit 0; daemon permission checked separately |

| 2026-10-12 00:36 | BE-02/1 | PASS | RED: routes missing 404, state module missing, migration columns missing; fixture corrected to source_record_id; exit 1 |

| 2026-10-12 00:39 | BE-02/2 | PASS | Additive 0028 schema; unique request and generation membership; separate baseline/overlay/golden/conflicts; workflow migration test GREEN |

| 2026-10-12 00:39 | BE-02/3 | PASS | ResolutionTask operation reference; existing lease/heartbeat/attempt history preserved; dispatch claim validates type/run identity |

| 2026-10-12 00:39 | BE-02/4 | PASS | GET workflow/operation and legacy mutation guards; create multipart workflow_version default 1; commit query workflow_version persisted across spawned import |

| 2026-10-12 00:39 | BE-02/5 | PASS | workflow contract + additive migration: 5 passed exit 0; single head 0028_session_workflow; older migration fixture uses latest ORM on 0018 and needs reflected inserts |

| 2026-10-12 00:40 | BE-03/1 | PASS | RED 5 failed: workflow_jobs missing; spies cover PREPARE/DECIDE/direct runner/streaming/resume/review reads; exit 1 |

| 2026-10-12 00:43 | BE-03/2 | PASS | Mechanical stop_after_decide on same compute/scoring/streaming; no FULL resume consumption; prepared normalized inputs reused |

| 2026-10-12 00:43 | BE-03/3 | PASS | Sealed hash-verified DECIDE DB artifact + immutable baseline overlay rows; insufficient sidecar preserved; annotations deferred until cluster |

| 2026-10-12 00:43 | BE-03/4 | PASS | Pair/bulk write overlays only; evaluator eligibility; scope/filter server; skipped and blocked counters |

| 2026-10-12 00:43 | BE-04/1 | PASS | RED finalize tests: 3 failed exit 1 missing finalize route/cluster_snapshot; pending bulk gate exposed by stages suite |

| 2026-10-12 00:48 | BE-03/5 | PASS | data/pairs paging; revision/replay/no_merge semantics and bulk active blockers validated; stages 5 GREEN |

| 2026-10-12 00:48 | BE-03/6 | PASS | stages+finalize+canonical preservation 9 passed exit 0; streaming/resume/direct DECIDE spies all zero cluster calls |

| 2026-10-12 00:48 | BE-04/2 | PASS | Decision snapshot sealed in separate transaction then short lock/recheck/enqueue; same request replay and stale second tab tested |

| 2026-10-12 00:48 | BE-04/3 | PASS | Existing build_proposals and shared reviewed_members; existing check_component/run_cluster_guards retained, REJECTED separate veto |

| 2026-10-12 00:48 | BE-04/4 | PASS | Singleton publication coverage 3 unique records; bridge conflict refuses generation publication |

| 2026-10-12 00:48 | BE-04/5 | PASS | Atomic generation/members/draft/state fenced publication; clusters GET before readiness 409; expired lease cannot publish |

| 2026-10-12 00:48 | BE-04/6 | PASS | finalize+stages+canonical baseline 9 passed exit 0; further concurrency/equivalence checks scheduled BE-06 |

| 2026-10-12 00:48 | BE-05/1 | PASS | RED golden integration 1 failed exit 1: legacy preview incorrectly accepts unconfirmed v2; persistence routes absent |

| 2026-10-12 00:52 | BE-05/2 | PASS | Golden server draft/selections/confirm; source membership and value validation; auto preserves manual by default; six engine-rule fields plus source-only extra fields |

| 2026-10-12 00:52 | BE-05/3 | PASS | Finalized result hash/coverage; legacy preview/export transport v2 adapter uses server choices; uploaded CSV checksum verified |

| 2026-10-12 00:52 | BE-05/4 | PASS | Explicit audited reopen invalidates current readiness, immutable historical export download retained; choices carried to next generation unconfirmed |

| 2026-10-12 00:52 | BE-05/5 | PASS | v1 workflow read-only adapter, default callers v1; old mutation paths on v2 guarded before side effects |

| 2026-10-12 00:52 | BE-05/6 | PASS | golden integration RED to GREEN: 1 passed exit 0; source/manual/auto/confirm/finalize/export/reopen/history covered |

| 2026-10-12 01:01 | BE-06/0 | IN_PROGRESS | QA Compose running on existing YAML; synthetic HTTP/worker and isolated PostgreSQL checks in progress; first unit regression aborted after detecting default .env provider configuration; rerunning isolated |

| 2026-10-12 01:15 | BE-06/0 | IN_PROGRESS | Compatibility 83 passed exit 0; workflow 29 passed exit 0. Found/fixed v1 readiness local import shadow and additive migration re-upgrade. Historical 0021 fixture updated to historical SQL; direct FULL/create_task v2 bypass guarded; manual golden helper parity RED reproduced then fixed. Final required regression and live QA pending. |

| 2026-10-12 01:21 | BE-06/1 | PASS | Workflow 30 passed / 793 deselected, required baseline regression 232 passed, legacy-worker-RBAC compatibility 83 passed, LLM contract/policy 58 passed; all exit 0. Historical migration seed uses historical SQL. Logs workflow.log/regression-final.log/compatibility.log/llm-contract.log; no skipped live tests. |

| 2026-10-12 01:21 | BE-06/2 | PASS | QA Neon isolated owned schema c6394650538b47b1be1d1c93762aae7e: 4 checks PASS exit 0, additive 0028 + old v1 preserved, competing finalize enqueue one success one409, expired lease cannot publish, golden competing writers one success one409; only schema cleanup, postgres.json/log. |

| 2026-10-12 01:29 | BE-06/3 | PASS | Live QA smoke PASS exit0 on final Compose API8001 + spawned worker same .env.qa Neon. Run 3afab16d-37a8-476d-a8ff-9642876eed26; CLUSTER109dfa0b one enqueue; 4 SUCCEEDED attempts; 3 memberships incl singleton; bulk accepted1/skipped2/blocked0; manual/source persisted fresh login; auto preserved; CSV R2 upload/checksum/download equal. qa-smoke.json/log. |

| 2026-10-12 01:29 | BE-06/4 | PASS | Canonical baseline copied byte-for-byte into portable fixtures; preservation final 2 passed exit0; protected source-final hashes unchanged except mechanical StageRunner. Auto/manual both selected profiles parity; core thresholds/evaluator/union/guard/survivorship unchanged. LLM QA true, selected/outbound/errors0; 58 policy/binding contract tests PASS, no live provider claim. |

| 2026-10-12 01:30 | BE-06/5 | PASS | Section1.4 actual routes/DTO and section6.1 handoff complete with QA IDs/hash/revisions/runtime/error/retry/legacy/limitations; source-final.json + backend-source.patch + portable canonical fixtures; handoff-check exit0 verifies runtime source SHA and DB match, final revisions14/3/1/5, actual OpenAPI17 paths. No FE diff/new Compose. |

| 2026-10-12 01:30 | BE-06/6 | PASS | All BE gates PASS: workflow30, baseline232, compatibility83, LLM58, preservation2, PostgreSQL4 checks, QA HTTP/worker/R2 smoke4 groups, source/runtime/OpenAPI handoff check, Ruff and diff-check exit0. FE NOT_STARTED; stop at BE-06; no commit/push/production deploy. |

| 2026-10-12 01:32 | FE-01/1 | PASS | BE-06 PASS reconfirmed read-only runtime/source/DB/OpenAPI gate; feature/feature_5 HEAD2caf945, full BE dirty/untracked preserved, no frontend diff initially. Same checkout required to consume uncommitted BE; no new worktree/subagents/approval as approved Prompt2. |

| 2026-10-12 01:33 | FE-01/2 | PASS | Selected IamNTN refs captured by git show only: 14 component/helper refs + 4 CSS modules + workflow.css; imports in 20261012-fe/donor-dependencies.json. Reuse shared ShellTopBar/ThemeToggle/SessionsHub/ResolutionIntake; adapt DTO, omit preview-cluster/repair/registry ranking/cache official choices. Ruling: approved design and dirty BE checkout remain in place; ledger in same plan, no delegation. |

| 2026-10-12 01:37 | FE-01/3 | PASS | RED client/navigation missing modules captured; typed client matches actual v2 endpoint/model bodies, shared cookie/CSRF transport, structured409 retained, no automatic mutation retry. Component RED tests added for hub/gates/eligibility/explicit finalize/golden source multivalue/unconfirmed. Baseline Vitest2 failures recorded separately. Upload prefix typo ruled /upload-sessions, API unchanged. |

| 2026-10-12 01:44 | FE-02/1 | PASS | RED hub/navigation and gates component coverage exists; upload wrapper remains tested by existing upload suite |

| 2026-10-12 01:44 | FE-03/1 | PASS | RED tests require explicit finalize, server eligibility, pending gate, no queued membership reads |

| 2026-10-12 01:50 | FE-01/4 | PASS | Selected shell/theme CSS and adapted workflow components compile; lint exit0, typed client3 GREEN and workflow8 GREEN; no dependency versions changed |

| 2026-10-12 01:50 | FE-02/2 | PASS | Existing resumable upload passes v2 commit query only when requested; default v1 preserved; URL draft hint/resume after upload, explicit prepare only |

| 2026-10-12 01:50 | FE-02/3 | PASS | Next async params routes and four gated URL tabs mounted; GET/poll aborted on unmount; owner auth shell; v1 history routes legacy |

| 2026-10-12 01:50 | FE-04/1 | PASS | RED cluster-not-ready/source selection/multivalue tests captured; stale revision intent regression added and GREEN |

| 2026-10-12 01:50 | FE-05/1 | PASS | RED unconfirmed DRAFT gate and no export tests; existing theme and viewer legacy tests retained |

| 2026-10-12 01:56 | FE-06/0 | IN_PROGRESS | lint exit0, Vitest68 PASS, build exit0; first browser QA failed login; rerun detected Docker daemon/API stopped. Restart existing Docker Desktop and same QA Compose; no new files/profile changes. |

| 2026-10-12 01:58 | FE-03/2 | PASS | Data raw/normalized/quality/source profile server paging; pairs baseline/review/evidence and p119_rule_v1 unit separate; Group/SubGroup filters server, page search labelled |

| 2026-10-12 01:58 | FE-03/3 | PASS | Pair revision, bulk decision revision, UUID/CSRF; server scope page or filter with exclusions; operation accepted/skipped/blocked shown; stale reload preserves intent without replay |

| 2026-10-12 01:58 | FE-03/4 | PASS | Only explicit allowed finalize click posts once then opens cluster progress; deterministic browser GET/tab/reload/BackForward zero mutations before clicks |

| 2026-10-12 01:58 | FE-03/5 | PASS | Unit68 passed including zero-candidate, insufficient GroupD permission, pending gates; deterministic3 PASS covers no implicit finalize and singleton display |

| 2026-10-12 01:58 | FE-04/2 | PASS | Cluster/member/evidence paging server; CLUSTER_RUNNING gate avoids reads; singleton and baseline conflict reasons remain visible; no repair/guard override |

| 2026-10-12 01:58 | FE-04/3 | PASS | Golden engine-selected fields/provenance/multivalues + source alternatives; explicit save/auto/confirm expected revisions; auto reset_manual omitted; extra fields source-only |

| 2026-10-12 01:58 | FE-04/4 | PASS | Server refresh retains local unsaved editor, detects revision and requires discard/reselect instead of silent rebase; no official localStorage; stale deterministic browser PASS |

| 2026-10-12 01:58 | FE-05/2 | PASS | Draft/confirmed/finalized labels and finalize blockers; export preview sends empty choices and uses server snapshot; no export until FINALIZED |

| 2026-10-12 01:58 | FE-05/3 | PASS | Utilities outside four tabs: exports history/audit capability; unsupported v2 split/simulate explicitly unavailable; no hidden mutation fallback |

| 2026-10-12 01:58 | FE-06/0 | IN_PROGRESS | Docker Desktop found in per-user Programs location; existing QA Compose restarted --no-deps. Fresh handoff check PASS same runtime source/DB/OpenAPI. Deterministic browser3 PASS; real browser QA retry next. |

| 2026-10-12 02:05 | FE-02/0 | IN_PROGRESS | Live browser found direct object-store transport unavailable after3 presign attempts; existing BE proxy supports same R2 session/checksum. Added FE fallback only network exhaustion, permanent checksum rejection unchanged; RED then upload12 GREEN. No BE change. |

| 2026-10-12 02:15 | FE-06/0 | IN_PROGRESS | Latest unit suite71 PASS. Docker daemon disappeared again after interrupted turn; QA smoke not claimed PASS. Restart same Compose and run browser in background with explicit exit-code file; checks continue independently. |

| 2026-10-12 02:18 | FE-02/4 | PASS | Unit upload12 and workflow10 GREEN; run-change/unmount abort initial/poll reads; deterministic3 PASS URL/BackForward, hub no restore, singleton/gates; full Vitest71 PASS; v1 deep-link adapter preserves explicit query and routes v2 away |

| 2026-10-12 02:18 | FE-06/0 | IN_PROGRESS | Review in place per explicit no-subagent instruction: fixed monotonically ordered workflow reads, synchronous double-click guard, final bulk result counters and audit capability. lint/build exit0, unit71 PASS; preservation1 PASS; current browser QA run reached DECIDE_REVIEW with PREPARE/DECIDE COMPLETED and no worker error. |

| 2026-10-12 02:21 | FE-06/0 | IN_PROGRESS | Latest frontend lint/build exit0 and full Vitest71 exit0; current browser QA confirmed source/manual extra choices, auto preserves manual, reload plus fresh context, then explicit Golden confirmation. Final export/history still running; no overall PASS yet. |

| 2026-10-12 02:27 | FE-06/0 | IN_PROGRESS | Live QA final CSV matches server, history fetch hit signed-storage redirect302 and no download; regression RED -> GREEN native browser download11 tests. Golden cards readable on mobile and dock padding added; rerun production build QA required. |

| 2026-10-12 02:29 | FE-04/5 | PASS | Golden tests GREEN; live browser reached confirmation/export after reload and independent fresh login context verified MANUAL_OVERRIDE name and SOURCE_CHOICE loyalty, auto preserved manual. Prior run2d635e32 CSV matched finalized server; remaining history transport tracked FE-06. |

| 2026-10-12 02:35 | FE-06/0 | IN_PROGRESS | Production QA completed source/manual/fresh context but confirmation409 retained choices and read server; now capture only conflict code/current/expected revisions, require explicit reviewed second confirmation if STALE_REVISION. Acceptance14/16 PASS; two new mock routes missed empty query suffix, fixed route matching. No BE change. |

| 2026-10-12 02:43 | FE-05/4 | PASS | Browser acceptance16 PASS including keyboard/error-read retry, mobile Golden multivalue/light-dark/no overflow and export button hit-test above dock. Final real QA desktop/mobile screenshots visually reviewed; source provenance readable cards, no raw JSON table. Native history CSV byte-equal export. |

| 2026-10-12 02:43 | FE-06/1 | PASS | Final lint/build exit0; full Vitest72 PASS; full deterministic acceptance16 PASS exit0 (six workflow + ten legacy/theme). QA uses production build, mocks explicitly separate from integration. |

| 2026-10-12 02:43 | FE-06/2 | PASS | Live Edge QA1 PASS exit0 in6.6min: .env.qa existing p119-session-qa API8001/worker and FE13000; login, resumable upload, v2 commit, data/DECIDE/manual/bulk/finalize/cluster/source/manual/auto/confirm/finalize/export/history/fresh context/legacy. |

| 2026-10-12 02:43 | FE-06/3 | PASS | Run4db76fbb: before finalize clusterops/generations/memberships/clusters all0; after exactly1 CLUSTER and1 generation,3 memberships,2 clusters incl singleton. Exactly1 browser finalize POST; MANUAL_OVERRIDE/SOURCE_CHOICE survive reload and fresh login. |

| 2026-10-12 02:43 | FE-06/4 | PASS | CSV562bytes matches finalized BE fields and all3 records, history byte-equal SHA256c4909f4e. Legacyd15c206f only GET and0 ResolutionTask. Real stale409 expectedworkflow10/current11 after reload during Auto was reviewed then second explicit confirm200; no FE replay. Backend46files unchanged, preservation1 PASS. |

| 2026-10-12 02:45 | FE-06/5 | PASS | All required exit codes0; final-review.json verifies frontend47-file hashes and protected BE46, same Session1 Compose and deleted QA Compose preserved. Reviewable frontend-source.patch/manifest, redacted network/conflict revisions, two visually reviewed screenshots and CSV/proof/QA JSON captured; no authenticated trace. |

| 2026-10-12 02:45 | FE-06/6 | PASS | Section6.2 final handoff completed with routes/client contracts, .env.qa reproduction commands, live run/revisions/hash/coverage, tests72+16+1 and preservation1, real stale review, legacy0 tasks and transport/LLM/volume limits. All acceptance checklist checked; no commit/push/production deploy. |

| 2026-10-12 02:46 | FE-04/0 | PASS | Final live QA1 PASS run4db76fbb: source/manual/extra choices survive reload and fresh authenticated context, Auto preserves manual; real stale409 read/review and explicit second confirm succeeded. Full workflow tests GREEN and browser acceptance16 PASS; no official localStorage choices. |

## 7. Hai prompt thực thi

### Prompt 1 — Session độc lập sửa BE

```text
Thực hiện Session 1 (BE-01 đến BE-06) trong docs/superpowers/plans/2026-10-12-session-workflow-be-first-fe-second.vi.md. Đây là chỉ dẫn thực thi luồng BE đã duyệt, không chỉ phân tích. Dùng superpowers:executing-plans và đọc thiết kế/constraints/checkpoint ngay trong file. Làm tuần tự hết phạm vi BE trong session này, không cần hỏi duyệt lại từng bước, chưa sửa FE và không tự phân công subagent.

Lấy feature/feature_5 làm nền, giữ nguyên thuật toán/policy/engine selection; chỉ tách điều phối và thêm workflow/persistence/API. PREPARE → BUILD_CANDIDATES/SCORE/DECIDE → dừng để review pair/Group/SubGroup/auto/bulk. Chỉ khi người dùng gọi finalize-decisions và completion hợp lệ mới đóng băng quyết định/enqueue CLUSTER. Giữ clustering/guard/survivorship nền, golden server/source/manual/auto/confirm/finalize/export và legacy compatibility. Không lấy manual_components/cluster_memberships hoặc field_registry ranking từ IamNTN để thay engine.

Dùng .env.qa cho QA runtime; API và worker cùng DB/profile, không đổi engine flags để xanh test. NEVER tạo file Docker Compose mới hoặc khôi phục file QA đang bị xóa; sửa docker-compose.yml hiện có. Bảo vệ dirty/untracked changes, không merge cả IamNTN, không log secrets, không push/deploy production.

Sau mỗi bước cập nhật checkbox và nhật ký ngay trong file kế hoạch; mỗi checkpoint chỉ PASS khi có evidence. Chạy preservation, workflow/legacy/worker/concurrency tests và synthetic API smoke QA. Hoàn tất phần bàn giao Session 1 với API/DTO/migration/runtime commands/tests và source diff để session FE đọc độc lập. Nếu gặp blocker, ghi đúng trạng thái và tiếp tục phần độc lập; không giả PASS. Kết thúc bằng link file, checkpoint đã đạt/chưa đạt và kết quả kiểm chứng. Dừng sau BE-06, không bắt đầu FE.
```

### Prompt 2 — Session độc lập sửa FE và nghiệm thu

```text
Thực hiện Session 2 (FE-01 đến FE-06) trong docs/superpowers/plans/2026-10-12-session-workflow-be-first-fe-second.vi.md. Đây là chỉ dẫn thực thi FE sau BE đã duyệt. Dùng superpowers:executing-plans, đọc toàn bộ thiết kế/constraints và bàn giao Session 1 trong file; không phụ thuộc lịch sử chat. Xác minh BE-06 PASS và API thực tế trước khi nối FE. Làm tuần tự hết phạm vi FE/tích hợp trong session này, không cần hỏi duyệt lại từng checkpoint, không tự phân công subagent.

Giữ nền feature/feature_5 và toàn bộ BE/engine đã hoàn thành. Lấy chọn lọc FE/components/CSS từ IamNTN bằng git show và dependency cần thiết; không merge/checkout cả nhánh, không mang thay đổi thuật toán hoặc auto-finalize sang. Chỉ sửa sau đăng nhập: Tổng quan upload/lịch sử → workspace phiên bốn tab Tổng quan dữ liệu, Phân loại các cặp bản ghi, Các nhóm bản ghi, Golden record. Nối Group/SubGroup/manual/auto/bulk, explicit finalize-decisions, progress/gates, cluster detail/golden field/source/auto/save/confirm, final/export/history/legacy. GET/tab/poll/reload không chạy cluster. Choices chính thức và eligibility lấy BE, không localStorage/luật FE.

Đọc frontend/AGENTS.md và Next guides đang cài. Dùng .env.qa và QA runtime API/worker cùng profile từ Session 1; NEVER tạo Compose mới hoặc khôi phục file QA đã xóa. Bảo vệ dirty changes, auth/quyền/theme/light-dark/responsive. Chỉ sửa BE nhỏ nếu phát hiện lỗi tích hợp trong contract đã duyệt, có regression và cập nhật bàn giao; không đổi engine/policy.

Sau mỗi bước cập nhật checkbox/nhật ký; mỗi checkpoint chỉ PASS khi có evidence. Chạy lint/Vitest/build, browser tests và synthetic browser end-to-end QA từ upload đến golden/export/reload/history, chứng minh cluster chỉ chạy sau explicit finalize và golden choices lưu server. Hoàn tất bàn giao Session 2 và checklist nghiệm thu ngay trong file. Ghi FAIL/BLOCKED/SKIP thật, không tự tuyên bố production-ready hoặc push/deploy. Kết thúc bằng link file, kết quả/checkpoint và giới hạn còn lại. Nếu BE-06 chưa đạt, hoàn tất prerequisite BE trong phạm vi đã duyệt trước khi FE và báo rõ phần còn lại; không bypass gate.
```
