# Session Workflow & Deployment Readiness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Cách thực thi cho tài liệu này:** mỗi prompt bên dưới chạy trong **một session riêng**, theo thứ tự S00 → S16; dùng `superpowers:executing-plans` để tự thực hiện trong session đó. Không tự tạo subagent. Người dùng hiện yêu cầu viết kế hoạch, chưa yêu cầu thực thi các session hay deploy production.

**Goal:** Đổi FE/BE sau đăng nhập thành quy trình theo phiên với bốn tab; backend chỉ chạy CLUSTER sau khi DECIDE được chốt; kiểm chứng đầy đủ trên Docker, PostgreSQL, R2 và LLM thật để có quyết định deploy dựa trên bằng chứng.

**Architecture:** Giữ Next.js, FastAPI, SQLAlchemy/PostgreSQL, worker và storage hiện tại. Tách xử lý tới DECIDE, duyệt/chốt quyết định, CLUSTER, survivorship và chốt/export thành các mốc có trạng thái, revision và output bền vững. Không viết lại matcher hoặc tách microservice ngoài phạm vi.

**Tech Stack:** Stack đang khai báo trong repo: Python 3.11, Next.js 16.3.8/React, Node 24 trong Docker, PostgreSQL 16, Alembic, PyArrow/DuckDB, Cloudflare R2, gateway Gemini/OpenRouter, pytest/Vitest/Playwright, Docker Compose. Đây là inventory source ngày 11/10/2026, không phải khuyến nghị phiên bản mới nhất.

**Spec:** [Thiết kế và hợp đồng đích trong cùng file](#1-thiết-kế-và-hợp-đồng-đích). Giữ một file theo yêu cầu người dùng; các contract này là thiết kế cần thực hiện, không phải API đã tồn tại.

**Ngày:** 11/10/2026, Asia/Bangkok. **Trạng thái:** kế hoạch; chưa thực thi, chưa chứng nhận READY.


> Execution update 2026-10-11: user authorized continuous S11-S16 and retained CSV-only/frozen TC5-smallest choices. S11/S12 functional QA PASS. S13-S16 required production/benchmark/release gates remain BLOCKED; final audit is NO-GO with R01-R08PASS and R09-R12BLOCKED. See docs/session-workflow/SESSION_STATUS.md and handoffs/S11-S16.md. Unchecked composite tasks below are not silently claimed complete. Final backend1163PASS/109SKIP; frontend74PASS; Linux mocked Chromium10/10PASS. No production deployment.

## Global Constraints

- Chỉ thay đổi giao diện sau đăng nhập; giữ luồng login/register và kiểm tra quyền hiện có.
- Tổng quan toàn ứng dụng có nút upload và lịch sử phiên. Một phiên tương ứng một `ResolutionRun`; giữ `run_id` và lịch sử cũ.
- Bốn tab: Tổng quan dữ liệu → Phân loại các cặp bản ghi → Các nhóm bản ghi → Golden record.
- Group/SubGroup phân loại **cặp**; cluster chứa **bản ghi** cùng thực thể. Không gộp toàn bộ một SubGroup thành một cluster.
- Không thực thi CLUSTER/build_proposals trước khi người dùng hoàn tất DECIDE. GET, chuyển tab và upload không được ngầm vượt điểm dừng này.
- Cho phép tự động giải quyết cặp đủ điều kiện; không buộc người dùng duyệt mọi candidate. Hard constraints và quyền phải được kiểm tra phía server.
- Golden có bản dự kiến theo luật; người dùng chọn từng trường hoặc auto chọn. Lựa chọn chính thức lưu BE, không chỉ localStorage.
- Kết quả bao phủ mọi bản ghi hợp lệ, kể cả singleton; bản ghi lỗi/quarantine được thống kê riêng, không âm thầm mất dữ liệu.
- Giữ auth, ownership, Origin/CSRF, optimistic revision, queue lease/fencing, audit, retry/idempotency và snapshot bất biến.
- Không tự sửa policy Group D thành một người/hai người hoặc auto approve. Dùng policy đã được quyết định; mặc định an toàn cho thiết kế mới là Group D không auto và không được manual merge khi chưa có policy được xác nhận. Người dùng vẫn có thể giữ riêng theo contract.
- Không bypass LLM policy để gọi thật. LLM hiện chỉ hỗ trợ annotation/giải thích, không tự nâng quyền auto merge hay tự chọn golden bằng cách sinh giá trị.
- Kiểm thử live chỉ dùng dữ liệu synthetic và tài nguyên QA riêng. Không migrate, fault-inject hoặc xóa dữ liệu production.
- Giữ toàn bộ thay đổi đã có của người dùng và checkpoint benchmark đang tạm dừng; không `reset --hard`, không ghi đè evidence cũ.
- Không in secrets, `.env` thật, signed URL, prompt chứa dữ liệu cá nhân hoặc full `docker compose config` có nội dung đã nội suy.
- Mỗi session chỉ thực hiện phạm vi được giao, ghi evidence và bàn giao; thiếu credential/target chỉ chặn phần phụ thuộc, tiếp tục phần độc lập.
- Không tự push/deploy production khi hoàn tất kế hoạch. Deploy staging trong tài nguyên QA được cấu hình là bước kiểm chứng; cutover production là bước riêng sau kết quả GO và chỉ dẫn của người dùng.

## Review Focus

| Điều kiện dễ bị bỏ sót | Hành vi phải đảm bảo | Session/test sở hữu |
|---|---|---|
| A–B và B–C được duyệt nhưng A–C bị cấm | Không tạo cluster vi phạm; báo conflict và quay lại duyệt | S04 `test_bridge_cannot_link_returns_conflict` |
| Hai tab gửi finalize hoặc quyết định đồng thời | Một operation, stale trả 409, không mất quyết định/override | S03/S04 `test_finalize_replay_and_stale_revision` |
| Mở phiên cũ và snapshot sau nâng cấp | Đọc được, không tự rerun/backfill quyết định người dùng giả | S01/S09 `test_legacy_run_and_snapshot_read_unchanged` |
| LLM bật nhưng không có cặp đủ điều kiện, hoặc output lỗi | Không tuyên bố live PASS; lỗi không đổi quyết định/merge | S10 `test_live_eligible_semantic_and_explanation` |
| R2 thiếu object/checksum sai khi resume hoặc export | Không dùng checkpoint giả; không publish output thiếu; lịch sử giữ nguyên | S11/S13 `test_missing_artifact_blocks_publication` |

---

## 0. Cách dùng, phạm vi và bằng chứng

### 0.1 Mỗi prompt là một session

1. Mở session mới tại repo `C:\VinUni\Dự án nhóm\P-119`.
2. Dán đúng một prompt Sxx. Prompt đã yêu cầu đọc file này, quy tắc chung và handoff trước đó; không cần mang toàn bộ chat cũ sang.
3. Agent kiểm tra prerequisites. Nếu session trước chưa PASS, xử lý blocker thuộc phạm vi hoặc báo rõ phần không thể tiếp tục; không tự đánh dấu PASS.
4. Sau session, xem handoff và diff. Session kế tiếp chỉ dùng contract/evidence đã tồn tại.
5. Session lỗi hoặc hết thời gian phải lưu checkpoint, process/container thuộc phạm vi và lệnh tiếp tục; không chạy lại toàn bộ từ đầu.

Các session triển khai được phép sửa code, chạy Docker QA, gọi R2/LLM thật trong giới hạn ghi ở đây **khi người dùng gửi prompt thực thi tương ứng**. Tài liệu này không tự chạy các việc đó.

### 0.2 Artefact bắt buộc cho mọi session

- `docs/session-workflow/SESSION_STATUS.md`: S00–S16, trạng thái `PASS / FAIL / BLOCKED / NOT_RUN`, dependency, commit/source hash, đường dẫn evidence.
- `docs/session-workflow/handoffs/Sxx.md`: phạm vi, thay đổi, contract, lệnh/exit code/kết quả, test skip và lý do, blocker, việc session sau cần đọc.
- `artifacts/session-workflow/<campaign-id>/Sxx/`: log đã redact, JUnit/JSON, browser trace/screenshot, metrics, manifest checksum. Không ghi đè lượt trước; campaign-id và attempt-id duy nhất.
- Manifest ghi source commit **và hash diff/untracked relevant**, image digest, migration head, profile flag, schema version, input hash và giờ chạy. Dirty HEAD không đủ mô tả bản đã test.
- Live evidence ghi run/job/attempt/generation/request ID và object key tương đối; không ghi token, signed URL hoặc credentials.
- Tóm tắt cuối session: done, tests đã chạy, phần chưa chạy, rủi ro còn lại, lệnh tiếp tục. Không biến `SKIP`, bị chặn hay cache hit thành live provider PASS.
- Tạo commit chỉ gồm file của session nếu phù hợp workflow đã chọn; repo đang có staged changes nên không dùng `git add .` hoặc commit toàn index. Nếu không cô lập được index an toàn, bàn giao patch/file list thay vì gom thay đổi của người dùng vào commit.

### 0.3 Các tài liệu hiện có phải đọc

| Nguồn | Tại sao cần đọc |
|---|---|
| `frontend/AGENTS.md` và `frontend/node_modules/next/dist/docs/` | Quy tắc Next đang cài, đọc guide liên quan trước sửa route/API |
| `docs/architecture/session-contract-cp01.md`, `session-navigation-cp03.md` | Quyền, đơn vị KPI, điều hướng cũ |
| `docs/merge/DECISIONS.md`, `CLUSTERING.md`, `GENERATIONS.md`, `CHECKPOINTS.md`, `WORKERS.md` | Baseline và invariant engine/worker/storage |
| `docs/decisions/GROUP_D_APPROVAL_DECISION.md` | Quyết định nghiệp vụ Group D còn chưa được chốt trong tài liệu cũ |
| `docs/qa/PRODUCTION_READINESS.md`, `FINAL_DEFECT_REGISTER.md`, `PRODUCTION_RUNBOOK.md` | Các blocker lịch sử cần xác minh lại, không coi đã hết |
| `docs/benchmark/2026-10-10-100k/CHECKPOINT.vi.md` | Benchmark cũ tạm dừng, giới hạn trước persist, lỗi LLM/schema và ingestion đã ghi |
| `.env.example`, `config.py`, `docker-compose*.yml`, `.github/workflows/ci.yml` | Cấu hình và cơ chế thực tế; không sao chép secret vào tài liệu |

**Các phát hiện làm đầu vào, chưa phải kết luận trên release mới:** DECIDE/CLUSTER đang nối liền; review pair tạo proposal/golden ngay; choices FE có luồng lưu localStorage; task hiện unique theo run; staged publication hiện lưu snapshot tổng hợp; QA Compose tắt LLM và dùng Moto; worker healthcheck tắt; Playwright gắn đường dẫn Edge Windows; CI Node 22 khác Docker Node 24. Báo cáo 100k cũ có lỗi schema với output cap 512, ingestion TC5 và recall/JUNK cần điều tra; không tái sử dụng nó để chứng nhận luồng mới.

## 1. Thiết kế và hợp đồng đích

### 1.1 Trải nghiệm người dùng

| Khu vực | Nội dung và hành động |
|---|---|
| `/app` — Tổng quan | Upload/tạo phiên; lịch sử phiên server pagination/search/filter; rename/archive/restore; không auto redirect phiên gần nhất |
| `/app/uploads/[uploadSessionId]` | Upload/validate/commit có thể tiếp tục sau reload; thể hiện đang tạo phiên. Sau commit điều hướng `run_id`, không tạo run giả khi chưa có input |
| `/app/sessions/[runId]/data` | Tổng quan phiên + tab dữ liệu; file/profile/schema, mẫu raw/normalized, lỗi/quarantine, chất lượng dữ liệu, PREPARE progress; nút bắt đầu phân loại |
| `/app/sessions/[runId]/decisions` | Tất cả kết quả DECIDE có bộ lọc Group/SubGroup và trạng thái; so sánh cặp, giải thích LLM, quyết định từng cặp/hàng loạt, completion blockers |
| `/app/sessions/[runId]/clusters` | Chỉ có kết quả sau finalize Decide và CLUSTER thành công; list/detail cluster, singleton, conflict, field choices, auto survivorship, confirmation |
| `/app/sessions/[runId]/golden` | Golden dự kiến/đã xác nhận với nhãn rõ; chỉ chốt/xuất kết quả cuối khi đạt readiness; history/comparison/download |
| Tiện ích phiên | Audit, lịch sử xuất, selective split/unmerge, analytics vẫn truy cập được; policy admin đặt trong khu vực phù hợp quyền, không thành bước bắt buộc của người dùng thường |

- Tổng quan phiên là header/layout chứa bốn tab; không cần tab thứ năm chỉ để lặp KPI.
- Tab chưa sẵn sàng vẫn xuất hiện với lý do; truy cập URL trực tiếp không bypass gate server.
- URL là nguồn trạng thái điều hướng, có Back/Forward, deep link, reload. Chuyển phiên xóa selection/draft không khớp member/revision, không làm mất draft đã lưu server.
- Không dùng `COMPLETED` của compute để hiển thị “Đã hoàn tất phiên”. Counters có đơn vị `file / record / pair / cluster / golden / snapshot`.
- Bấm auto tại Decide nghĩa là áp dụng quyết định cho cặp đủ điều kiện, không phải thực thi merge dữ liệu/cluster ngay.

### 1.2 Máy trạng thái

Giữ `ResolutionRun.status` cho compute compatibility. Thêm `workflow_version=2`, `workflow_state`, `workflow_revision`, `decision_revision`, `cluster_revision`, `golden_revision`. Các revision khởi đầu 0; workflow_revision tăng mỗi mutation nghiệp vụ. Version 1 là phiên lịch sử, không giả định đã có finalize mới.

| `workflow_state` | Ý nghĩa | Transition chính |
|---|---|---|
| `DATA_PREPARING` | Input committed, PREPARE chưa hoàn thành | PREPARE success → `DATA_READY` |
| `DATA_READY` | Đã có normalized/profile checkpoint | start-decide → `DECIDE_RUNNING` |
| `DECIDE_RUNNING` | BUILD/SCORE/DECIDE đang chạy | pair publication success → `DECIDE_REVIEW` |
| `DECIDE_REVIEW` | Có kết quả cặp để duyệt | finalize-decisions → `CLUSTER_QUEUED` |
| `CLUSTER_QUEUED` | Snapshot quyết định đã khóa, job đang chờ | claim → `CLUSTER_RUNNING` |
| `CLUSTER_RUNNING` | Tạo cluster từ snapshot cố định | success → `GOLDEN_REVIEW`; conflict → `DECIDE_REVIEW` |
| `GOLDEN_REVIEW` | Cluster hợp lệ, golden draft/choices đang được xác nhận | finalize-golden → `FINALIZED` |
| `FINALIZED` | Kết quả nghiệp vụ được chốt | export snapshot; reopen có chủ đích → `DECIDE_REVIEW` |
| `LEGACY_READ_ONLY` | Phiên cũ đọc theo adapter | Không tự rerun; chuyển sang luồng mới bằng tạo phiên mới từ input được phép |

- Failure nằm trên operation (`FAILED / RETRYABLE_FAILED` + stage + error_code), workflow giữ checkpoint bền vững cuối cùng; không có một `FAILED` mất hết ngữ cảnh.
- Lệnh start/finish transaction phải cập nhật state và enqueue cùng atomic boundary. Operation thất bại trước/đang DECIDE retry đúng stage, không dùng output chưa seal.
- Review mutation chỉ khi `DECIDE_REVIEW`. Trong lúc cluster queued/running, decision snapshot bất biến; mutation trả 409.
- Reopen sau cluster/chốt tăng revision, vô hiệu hóa active cluster/golden cho mutation/export mới nhưng vẫn giữ bản trước và snapshot lịch sử. Quyết định được giữ nếu input/policy/evidence còn khớp.

### 1.3 Ý nghĩa quyết định và Group D

- Baseline rule decision, routing, evidence, auto/manual eligibility là các trường riêng, không suy eligibility từ nhãn Group.
- API decision mới: `MERGE`, `KEEP_SEPARATE`, `NON_MATCH`, `DEFER`.
- `MERGE`: lưu cạnh được chấp nhận, không tạo cluster ngay.
- `KEEP_SEPARATE`: không áp dụng cạnh ghép của cặp đó trong phiên; không chứng minh khác thực thể và không tự sinh cannot-link toàn nhóm. UI giải thích bản ghi vẫn có thể nối gián tiếp; nếu muốn cấm cùng cluster dùng `NON_MATCH`.
- `NON_MATCH`: ràng buộc không được cùng cluster, lưu negative edge có provenance.
- `DEFER`: chưa giải quyết, tiếp tục là completion blocker. Các trường hợp thiếu dữ liệu có thể giữ riêng minh thị theo policy thay vì treo phiên mãi.
- Không remap `REJECTED` cũ thành `NON_MATCH`; có adapter rõ cho legacy semantics.
- Cặp đã auto-resolved theo eligibility không cần người dùng bấm từng cặp; vẫn xem, kiểm tra và override được trong Decide khi đủ quyền.
- Group D: cần quyết định release thực tế ở S00/S01. Nếu chưa có, áp dụng mặc định mới không auto/manual merge D; giữ riêng/non-match có kiểm soát. Không công bố hỗ trợ four-eyes khi chưa có chia sẻ phiên và hai actor khác nhau.

### 1.4 Completion, cluster conflict và golden

`finalize-decisions` chỉ thành công khi DECIDE generation đầy đủ/durable; không còn `DEFER`/pending bắt buộc; bulk job đã xong; không có conflict đã biết chưa giải quyết; expected revision đúng. Không dùng điều kiện “mọi pair user_decision khác UNDECIDED” vì cặp auto/non-match theo rule có thể đã resolved.

CLUSTER thực thi toàn bộ guard: identity/type, explicit cannot-link, policy limits, source constraints và cluster bridge. Mâu thuẫn mới phát hiện khi tạo nhóm được lưu thành conflict riêng với involved pair/record IDs, không sửa baseline evidence. Cluster conflict khiến phiên quay lại `DECIDE_REVIEW`, chưa công bố bộ cluster cuối. Giới hạn tài nguyên/timeout là job failure rõ ràng, không tự loại cạnh để giả success.

Golden draft chỉ sinh sau cluster hợp lệ. Mọi accepted record thuộc đúng một active cluster, kể cả cluster một thành viên. Golden field có `value`, `source_record_id`, `source_system`, `selection_mode` (`RULE / SOURCE_CHOICE / MANUAL_OVERRIDE`), `rule_version`, `chosen_by`, `chosen_at`; hỗ trợ nhiều observation cho phone/email và primary value khi export scalar.

Schema registry gồm `name, phone, email, address, dob, government_id` và các trường bổ sung được ánh xạ/giữ lại từ input. Trường nào không đủ nghĩa để hợp nhất phải được đánh dấu không hỗ trợ/needs review, không âm thầm bỏ. Trường cấu trúc liên quan có luật chọn nhất quán; không ghép phường/tỉnh/đường thành địa chỉ chưa tồn tại trong nguồn. UI và BE cùng dùng registry, không hardcode chỉ bốn trường.

Auto chọn theo luật từng trường: lọc thiếu/invalid/placeholder trước; xét verified/chất lượng, ưu tiên nguồn theo trường, timestamp nguồn thực và tie-break ổn định. Frequency không thắng giá trị đã được xác minh chỉ do bị lặp; không dùng import time làm source_updated_at. Manual choice/override được giữ khi auto lại, trừ `reset_manual=true` minh thị. Lưu tất cả alternatives/provenance phục vụ truy nguyên và split.

Auto-select không tự confirmation: nút riêng xác nhận một cluster hoặc xác nhận hàng loạt các golden đủ điều kiện. Finalize yêu cầu tất cả golden active được xác nhận, mandatory fields hợp lệ hoặc thiếu có lý do được policy cho phép, không có draft/stale revision/conflict. Các field optional được null hợp lệ.

### 1.5 API đích dưới `/api/v1/mdm/runs`

| Method/path | Input quan trọng | Output/trách nhiệm |
|---|---|---|
| GET `/{run_id}/workflow` | — | `WorkflowRead`: state/revisions/stage statuses/counts/blockers/allowed_actions |
| GET `/{run_id}/data-profile` | pagination cho samples | Profile/schema/quality/quarantine, raw và normalized sample |
| POST `/{run_id}/start-decide` | `request_id`, `expected_workflow_revision` | 202 `OperationRead`, chỉ từ DATA_READY |
| GET `/{run_id}/pairs` | group/subgroup/status/limit/cursor | Tất cả DECIDE pairs, evidence, resolution, eligibility, revision |
| GET `/{run_id}/pairs/categories` | — | Group/SubGroup counts theo pair; primary partition và matched tags phân biệt |
| POST `/{run_id}/pairs/{pair_id}/decision` | `request_id`, `expected_pair_revision`, decision, reason | Pair decision + mới revision; audit; không tạo proposal |
| POST `/{run_id}/pairs/bulk-decisions` | `request_id`, expected revision, filter snapshot hoặc IDs, decision | 202 operation; resolved/blocked/stale counts, chỉ eligibility hợp lệ |
| POST `/{run_id}/finalize-decisions` | `request_id`, `expected_decision_revision`, `expected_workflow_revision` | Atomic close + immutable decision snapshot + enqueue CLUSTER; 202 |
| POST `/{run_id}/reopen-decisions` | `request_id`, expected workflow revision, reason | Vô hiệu active derived results, giữ historical snapshots |
| GET `/{run_id}/clusters` và `.../{cluster_id}` | pagination/detail | Active cluster revision, singleton, members, links, field options |
| GET `/{run_id}/clusters/conflicts` | — | Conflict với ID ổn định và involved IDs; route static trước dynamic |
| PUT `/{run_id}/clusters/{cluster_id}/golden` | `request_id`, `expected_cluster_revision`, `expected_golden_revision`, field selections | Persist choices/overrides server, tăng revision, bỏ confirmation cũ |
| POST `/{run_id}/golden/auto-select` | `request_id`, expected revisions, scope, `rule_version`, `reset_manual=false` | Operation nếu scope lớn; cập nhật draft, không overwrite manual mặc định |
| POST `/{run_id}/clusters/{cluster_id}/golden/confirm` | request/revision | Xác nhận draft hiện tại |
| POST `/{run_id}/golden/confirm-eligible` | request/revisions/filter snapshot | Bulk confirm chỉ các golden đủ điều kiện, audit từng item |
| GET `/{run_id}/golden-records` | cursor/status/q | Draft/confirmed golden + cluster revision/provenance |
| POST `/{run_id}/finalize-golden` | request/expected workflow và golden revision | Chốt immutable business result, không tự export |
| GET `/{run_id}/operations/{operation_id}` | — | `OperationRead`: type, status, stage, attempt, counters, error |

Giữ các API auth/upload/export/audit/split/policy hiện có; cập nhật contract hoặc adapter rõ. API legacy `/start` không được giữ đường chạy qua cluster cho run v2. Preview/export mới dùng finalized revision; snapshot cũ tải đúng bytes. Các endpoint mới phải được đăng ký router thực tế, không chỉ tồn tại trong module/test unit.

`WorkflowRead` bắt buộc: `run_id`, `workflow_version`, `workflow_state`, bốn revision (`workflow_revision`, `decision_revision`, `cluster_revision`, `golden_revision`), `active_operation`, `stages`, `counts` có unit, `blockers` chứa `code/message/related_ids`, `allowed_actions`. `OperationRead` bắt buộc: `operation_id`, `run_id`, `operation_type`, `request_id`, `status`, `phase`, `attempt_id`, progress, error, timestamps. Output snapshot/golden phải trả revision để FE chặn stale.

HTTP: 401 chưa login; 403 thiếu capability; 404 không sở hữu/not found; 409 stale/state/conflict; 422 invalid input; 202 async accepted; replay request_id cùng payload trả operation/result cũ, khác payload trả 409. GET không mutation. Mutation dùng existing CSRF/Origin + ownership + capability; action mới được ánh xạ rõ vào quyền hiện tại, không tự cho Viewer quyền ghi.

### 1.6 Persistence và orchestration

- Tái sử dụng một `ResolutionTask` active slot per run (hiện unique run_id). Thêm `operation_id`/type; terminal slot chỉ được tái dùng trong transaction khóa run. `ResolutionOperation` mới giữ durable request/idempotency/history; attempt lưu operation identity để retry không bị nhầm giữa DECIDE và CLUSTER. Không drop unique task chỉ để chạy nhiều stage cùng run.
- Thêm mô hình trong `mdm/workflow_models.py`: `ResolutionOperation`, `ResolutionDecisionSnapshot`, `ResolutionClusterGeneration`, `ResolutionClusterMember`, `ResolutionWorkflowConflict`, `ResolutionGoldenDraft`, `ResolutionGoldenSelection` và `ResolutionDataProfile`. Mapping tới proposal/entity/snapshot existing qua adapter; không lập hệ entity thứ hai.
- Decision snapshot lưu manifest + input/config/policy/evidence/review hash và revision; dữ liệu lớn ở R2 Parquet, serving pairs ở PG. Cluster membership unique theo `(run_id, cluster_revision, record_id)`; generation chứa pointer tới decision snapshot cụ thể.
- Tách publish pair output sau DECIDE khỏi publish cluster/golden. Các staging/checkpoint feature phải hiểu phase-specific outputs. Existing insert-only `persist_snapshot` không đủ cho recompute/reopen; cần reconciliation versioned, giữ human state/provenance/history.
- R2 artifact chỉ được dùng sau receipt/checksum/complete manifest hợp lệ. Storage naming nằm sau adapter: một QA prefix có run/operation/attempt/stage identity; không đổi đường dẫn lịch sử bằng rename toàn bucket.
- LLM annotation gắn baseline pair/evidence hash trước cluster; không đợi cluster mới có giải thích ở tab Decide. Cluster risk là dữ liệu bổ sung riêng, không sửa evidence để giữ annotation giả fresh.
- Rollout bằng workflow_version; engine flags vẫn pinned theo run. Không đổi mặc định thuật toán/threshold để khiến test mới pass.

### 1.7 Bản đồ file

| Phần | Existing cần sửa/tận dụng | New dự kiến |
|---|---|---|
| Workflow/DB/API | `mdm/models.py`, `schemas.py`, `api/mdm_runs.py`, `api/mdm_resolution.py`, `main.py`, migrations | `mdm/workflow_models.py`, `workflow_contracts.py`, `workflow_service.py`, `workflow_jobs.py`, `api/mdm_workflow.py` |
| Compute/publication | `resolution_service.py`, `resolution_worker.py`, `job_queue.py`, `resolution_persistence.py`, `generation_staging.py`, `pipeline/stage_runner.py`, `streaming.py`, `checkpoints.py` | `mdm/decision_publication.py`, `cluster_publication.py`, `data_profile.py` |
| Review/cluster | `resolution_pairs.py`, `resolution_items.py`, `pipeline/decision_semantics.py`, `clustering.py`, `cluster_guards.py`, `review_grouping.py` | `mdm/workflow_review.py`, `workflow_clusters.py` |
| Golden | `pipeline/golden.py`, `golden_v2.py`, config policy, `export_stream.py`, preview/import/split modules | `mdm/golden_service.py`, `field_registry.py` |
| FE | `ResolutionWorkspace`, `AppShell`, `SessionsHub`, `ResolutionIntake`, `PairReviewFlow`, `ResolutionFlow`, `api.ts`, `contracts.ts`, `sessionView.ts`, `resolutionChoices.ts` | route layout/pages; `SessionWorkspace`, `SessionTabs`, `DataOverview`, `DecisionWorkspace`, `ClusterWorkspace`, `GoldenRecords`, `GoldenFieldEditor` |
| QA/ops | Dockerfiles/Compose, config, Playwright config, CI, existing QA scripts | `docker-compose.workflow.yml`, `docker-compose.workflow.live.yml`, live Playwright config/specs, `scripts/qa_workflow*.py`, release evidence |

New files trong bảng là quyết định phân ranh giới; nếu kiểm tra S00 thấy module tương đương đã có, dùng lại và ghi cập nhật contract/file map trước khi triển khai. Không tạo wrapper trùng chức năng chỉ để khớp tên tài liệu.

## 2. Môi trường kiểm thử và các gate

### 2.1 Ba tầng kiểm thử

| Tầng | Mục tiêu | Có chứng minh live không? |
|---|---|---|
| Unit/contract | Máy trạng thái, eligibility, revisions, tính toán, failure injection deterministic | Không; mocks hợp lệ ở tầng này |
| Docker integration local | FE production build + API + worker + PG thật + S3 emulator; replay/restart/transactions | PG/container thật; Moto không chứng minh R2 thật |
| Live QA/staging | Docker packaged runtime + R2 Cloudflare + LLM provider thật + trình duyệt qua API; HTTPS staging | Có, khi có receipts/checksum/outbound success thật |

Full-feature tests cần kiểm tra trực tiếp effect và dữ liệu sau reload/restart. Smoke provider trả 200, `/health` xanh hoặc `APP_ENV=production` trên localhost không thay cho test nghiệp vụ/deploy staging.

### 2.2 Credentials, chi phí và tài nguyên

- S00 lập inventory chỉ tên biến/presence, tài nguyên QA ownership và scope; không in giá trị. Không tự overwrite `.env`, `.env.qa` đang mở. Dùng env file riêng git-ignored; secret không copy sang worktree ngoài cơ chế đã xác nhận.
- PostgreSQL QA riêng/schema riêng xác minh `current_database/current_schema`; R2 bucket QA riêng hoặc prefix độc quyền **có storage adapter cưỡng chế prefix**. Nếu chưa có prefix enforcement, bắt buộc bucket riêng.
- Live LLM ở S10/S12/S14/S15/S16 chỉ synthetic. Budget khởi đầu campaign smoke dùng chung S10/S12/S15/S16: **tối đa 20 outbound attempts, 100.000 tokens, 1.000.000 microUSD reservation (1 USD)**, tính tất cả retry/fallback; áp dụng shared campaign accounting để đổi run hoặc session không reset budget. Đây là trần đề xuất cho kế hoạch, không chứng nhận chi phí hóa đơn thực; nếu cần nâng trần phải hỏi đúng phần tăng. Giá cấu hình phải có nguồn thật/upper bound rõ, usage chưa biết tính bảo thủ.
- 100k benchmark LLM có ngân sách riêng phải chốt trước S14; không lấy giới hạn 20 attempts smoke làm chứng nhận load LLM production, không tự nâng cap để chạy hết dữ liệu.
- Gateway hiện dùng `LLM_ENABLED`, `SEMANTIC_ADDRESS_ENABLED`, `LLM_EXPLANATION_ENABLED`, provider/model allowlist, retention contract, giá và DB `LLMPolicyControl`. Không chỉ bật env; không ghi `*_RETENTION_VERIFIED=true` nếu chưa có chứng cứ phù hợp dữ liệu/account.
- Gemini primary; OpenRouter backup theo contract hiện tại khi quota/cooldown. Không cố tiêu hết quota thật để test fallback; test negative transport bằng fault injection và live backup bằng QA cooldown hợp lệ. Timeout/5xx không tự chuyển provider nếu có rủi ro bill hai lần.
- Nếu backup là phần release profile, phải có credential và một request backup thật thành công. Nếu chỉ release Gemini, tắt backup minh thị và ghi phần unsupported vào release profile, không tuyên bố cả hai đã kiểm chứng.
- Các lệnh fault/restart/cleanup chỉ tác động project/container/schema/prefix do campaign tạo. Không `docker system prune`, không `down -v`, không xóa bucket shared. Artefact đang tham chiếu phải được giữ để phục hồi.

### 2.3 Bộ lệnh chuẩn sẽ được thiết lập ở S00

Để test không đọc `.env` phát sinh live call/migration ngoài ý muốn, S00 mở rộng runner hiện có với `--paths` cho pytest paths và thêm `backend/tests/test_llm` vào isolation verification. Đây là CLI **dự kiến**, chỉ dùng sau khi S00 đã implement và chứng minh.

```powershell
# Từ repo root, sau S00; runner dùng environment cô lập và lưu JUnit.
.venv\Scripts\python.exe -X utf8 scripts\run_merge_baseline.py --paths backend/tests/test_mdm/test_workflow_contract.py --timeout 1200
.venv\Scripts\python.exe -X utf8 scripts\run_merge_baseline.py --suite all --timeout 1200
.venv\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations scripts/qa_workflow.py

# Chạy từng lệnh dưới đây với working directory frontend.
npm test
npm run lint
npm run build
npx playwright test --config playwright.workflow.config.ts

# Compose workflow file/env QA riêng sẽ được tạo; không dùng nhầm compose mặc định.
docker compose --env-file .env.workflow.qa.local -p masterone-workflow -f docker-compose.qa.yml -f docker-compose.workflow.yml config --quiet
docker compose --env-file .env.workflow.qa.local -p masterone-workflow -f docker-compose.qa.yml -f docker-compose.workflow.yml up -d --build
```

`qa_workflow.py` là harness dự kiến sở hữu các subcommand `preflight`, `api`, `storage`, `llm`, `acceptance`, `faults`, `restore`, `benchmark`, `staging`, `audit`. Tách module khi lớn; đọc config bằng parser/env an toàn, nhận `--campaign <id> --output <owned-dir>`; mode live bắt buộc scope QA/target manifest/budget. Mỗi command trả exit nonzero nếu bất kỳ required check FAIL/BLOCKED; JSON ghi từng assertion. Không kế thừa lỗi verifier cũ exit 0 trong khi assertion FAIL.

### 2.4 Release gates bắt buộc

| Gate | Điều kiện PASS | Session |
|---|---|---|
| R01 UX | Tổng quan + upload/lịch sử + bốn tab, reload/deep link, quyền và trạng thái rõ | S06–S08, S12 |
| R02 workflow | Không CLUSTER trước finalize; decisions durable; snapshots versioned/idempotent | S01–S04 |
| R03 dữ liệu | Coverage, unique active membership, constraints/bridge, singleton/quarantine | S02/S04/S14 |
| R04 golden | Mọi field hỗ trợ, auto/manual provenance, server persistence, confirmed revision | S05/S08/S09 |
| R05 auth/history | Roles/owner/CSRF; legacy read; immutable export; audit/split/policy | S03/S09/S12 |
| R06 LLM thật | Eligible semantic + explanation SUCCESS qua app; UI consume; budgets/cache/fallback kiểm chứng | S10/S12 |
| R07 R2 thật | Browser direct upload, stage checkpoints, snapshot/download checksum, restart durability | S11–S13 |
| R08 Docker | Clean production images, explicit migration, separate worker healthy, no host checkout dependency | S00/S12/S15 |
| R09 recovery/ops | Expired lease/retry/fencing, DB/R2/LLM failure, backup restore, alerts có delivery | S13/S15 |
| R10 chất lượng/capacity | Independent gold, ingest toàn bộ source, precision/recall strata, approved SLO đạt | S14 |
| R11 checks | Required lint/test/build/migration checks + portable browser CI pass; không bỏ lỗi cũ | S15/S16 |
| R12 staging/release | HTTPS/cookies/origins thực, digest/config đúng profile, rollout/rollback thực tập, blockers đóng | S15/S16 |

**GO chỉ khi tất cả gate bắt buộc PASS trên cùng candidate/profile.** Mất credentials, chưa có SLO/quality target, policy D chưa quyết định cho tính năng được quảng bá, model không dùng được, hoặc staging chưa chạy đều không được tự đổi thành PASS. Có thể xác nhận “QA feature-complete” với giới hạn, nhưng phải phân biệt với “production GO”.

## 3. Lộ trình theo session

| Session | Deliverable | Phụ thuộc |
|---|---|---|
| S00 | Inventory/baseline, QA Docker scaffold, isolated runner, quyết định release scope | — |
| S01 | DB workflow/contracts/migration/operation history | S00 |
| S02 | PREPARE và DECIDE riêng, publish cặp trước cluster | S01 |
| S03 | Pair/bulk review, finalize/reopen, completion gates | S02 |
| S04 | Cluster deferred, immutable snapshot, conflict/recompute | S03 |
| S05 | Field registry, server golden choices, survivorship/confirmation | S04 |
| S06 | FE Tổng quan, lịch sử/upload, session layout/routes | S01/S02 |
| S07 | FE dữ liệu + phân loại Group/SubGroup | S03/S06 |
| S08 | FE cluster editor + golden cuối | S05/S07 |
| S09 | Export/legacy/history/split/audit/policy regression | S04/S05/S08 |
| S10 | LLM integration và live bounded evidence | S03/S07/S09 |
| S11 | R2 direct upload/checkpoints/export thật | S02/S04/S09 |
| S12 | Production Docker + browser E2E mọi tính năng, LLM/R2 thật | S06–S11 |
| S13 | Recovery, backup/restore, alerting và soak | S12 |
| S14 | Chất lượng + 100k full publish workflow + capacity | S12/S13 |
| S15 | CI/release images, container hardening, HTTPS staging | S12–S14 |
| S16 | Independent verification/release gates + bàn giao deploy | S00–S15 |

Thực thi tuần tự; dependency bảng chỉ để biết contract nào cần đọc. Không khởi chạy nhiều session cùng sửa shared files.

### S00 — Hiện trạng, baseline và môi trường QA

**Files:** đọc các nguồn ở 0.3; sửa `scripts/run_merge_baseline.py`; tạo `backend/tests/test_workflow_runner.py`, `docker-compose.workflow.yml`, `scripts/qa_workflow.py`, `docs/session-workflow/SESSION_STATUS.md`, `docs/session-workflow/environment-manifest.example.json`, `docs/session-workflow/release-profile.md`, handoff S00.

**Interfaces:** CLI isolated `--paths` và manifest campaign không secrets; Compose project `masterone-workflow` dùng ports dự kiến PG15447/storage19017/API18017/FE13017, kiểm tra đang trống trước start. Override frontend build URL, CORS và R2 presign nhất quán; YAML/Compose version phải hỗ trợ syntax override thực tế.

- [ ] Ghi inventory source/index/diff, router mounted, migration heads, flags, datasets và existing QA containers; đọc benchmark paused nhưng không resume.
- [ ] Thêm test runner isolation chứng minh live credentials/LLM switches/R2 không lọt vào unit tests; mở rộng `--paths` giữ CLI cũ, reject path ngoài repo.
- [ ] Lập release profile: Group D, input formats/source contracts, field mapping, engine flags, provider/model, QA scope, scale/SLO/quality targets. Ghi rõ dữ liệu cần người dùng quyết định; không biến đề xuất thành approval.
- [ ] Tạo QA Compose isolated, validate `config --quiet`, khởi Docker Desktop nếu chưa chạy bằng background hidden, rồi `up -d --build`; không restart stack khác. LLM tắt, Moto baseline không tính live R2.
- [ ] Chạy backend baseline all, FE test/typecheck/build, migration inventory và health/readiness; lưu lỗi kế thừa thành defect có reproduction.
- [ ] Ghi evidence/handoff S00 và trạng thái gate; không claim deploy-ready.

**Tests:** `test_runner_redacts_and_isolates_live_environment`; `test_runner_paths_reject_outside_repo`; baseline exit/assertion counts; Docker services/image identity; actual migration head single được xác minh.

**Prompt — session S00:**

```text
Thực hiện riêng S00 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md trong repo P-119. Đọc Global Constraints, phần 0–2 và S00 trước khi hành động; dùng superpowers:executing-plans. Tôi muốn inventory và baseline thật, QA Docker riêng được mở/chạy, runner test cô lập hỗ trợ --paths và bàn giao để triển khai workflow. Giữ mọi staged/unstaged changes và benchmark paused; không đọc/in secrets, không resume benchmark, không tác động production. Xác định release profile cùng các quyết định còn thiếu (Group D, SLO, dataset formats, provider/model, staging target), hỏi ngắn nếu cần và tiếp tục công việc độc lập. Chạy baseline hiện có, ghi lỗi thật không bỏ test. Tạo evidence và handoff S00, cập nhật SESSION_STATUS.md; chỉ triển khai scaffold QA/test harness của S00, chưa sửa pipeline hay FE nghiệp vụ.
```

### S01 — Contract, máy trạng thái và migration

**Files:** tạo `mdm/workflow_models.py`, `workflow_contracts.py`, `workflow_service.py`, `api/mdm_workflow.py`; sửa models imports/migration bootstrap, schemas/router registration, `models.py`, `job_queue.py`; tạo migration số kế tiếp sau heads đã xác minh, tests `test_workflow_contract.py`, `test_workflow_migrations.py`.

**Interfaces:** các field/state/DTO/API 1.2/1.5/1.6; `get_workflow(db, run_id, actor) -> WorkflowRead`; `begin_operation(db, run_id, operation_type, request_id, payload_hash, expected_revision, actor) -> OperationRead`. Mọi pending operation có identity riêng; unique task slot giữ nguyên.

- [ ] Viết regression thất bại cho version2 state/revisions, allowed_actions/owner, request replay payload khác và legacy version1 read.
- [ ] Chạy targeted tests RED bằng runner S00, xác nhận fail do tính năng chưa có.
- [ ] Implement schema/DTO và transaction helper; migrate legacy additive, không giả quyết định đã duyệt. Unknown states fail closed ở FE/API.
- [ ] Migrate DB QA từ head cũ, migrate DB rỗng, kiểm tra model metadata registered và single head; downgrade rehearsal trên clone nếu migration hỗ trợ, không downgrade DB có user data.
- [ ] Chạy targeted tests GREEN và PG transaction/unique constraints thực; schema lock/rollback không làm hỏng legacy export.
- [ ] Lưu API contract/diagram trong handoff S01; bổ sung release-profile nếu có quyết định của người dùng.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_contract.py backend/tests/test_mdm/test_workflow_migrations.py`; `test_operation_replay_payload_mismatch_409`, `test_legacy_run_and_snapshot_read_unchanged`, `test_one_active_task_per_run`, `test_cross_owner_workflow_404`.

**Prompt — session S01:**

```text
Thực hiện riêng S01 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md. Đọc quy tắc chung, contract đích và handoff S00; dùng superpowers:executing-plans. Implement máy trạng thái/revisions, OperationRead/WorkflowRead, operation history/idempotency, API workflow và migration additive trên QA. Giữ unique ResolutionTask theo run, attempt gắn operation, auth/owner/CSRF và lịch sử version1. Chọn migration số kế tiếp bằng kiểm tra head thực tế, không hardcode 0029 nếu đã có migration mới. Test RED/GREEN, chạy upgrade từ clone head cũ và DB QA rỗng, chứng minh một active task/run. Không chạy pipeline/LLM live hay đổi giao diện. Ghi evidence, handoff S01 và SESSION_STATUS.md.
```

### S02 — Tách PREPARE/DECIDE và công bố kết quả cặp

**Files:** sửa `resolution_import.py`, upload/import jobs, `resolution_service.py`, `pipeline/stage_runner.py`, `streaming.py`, `checkpoints.py`, `generation_staging.py`, `resolution_persistence.py`; tạo `data_profile.py`, `decision_publication.py`, `workflow_jobs.py`; tests `test_workflow_stages.py`, `test_decision_publication.py`.

**Interfaces:** `run_prepare(operation_id, factory, progress) -> None`; `run_decide(operation_id, factory, progress) -> None`; `publish_decisions(db, run_id, operation_id, generation_manifest) -> DecisionPublicationRead`. Entrypoints `start-decide`/legacy start run-v2 chỉ tới DECIDE. `data-profile` durable và sample bounded.

- [ ] Viết test fail đặt spy vào `build_proposals`: upload/prepare/start-decide v2 kết thúc mà spy call count bằng 0, kể cả large StageRunner và streaming profile.
- [ ] Chạy RED; thêm trường hợp DECIDE pairs/profile đọc được trước cluster, restart mất scratch vẫn đọc/resume bằng artifact.
- [ ] Tách stage/service/worker dispatcher; upload commit enqueue PREPARE, success DATA_READY; user start-decide mới BUILD/SCORE/DECIDE. Không normalize hai lần hay đổi rule baseline.
- [ ] Publish pairs/evidence/Groups trước cluster qua phase-specific seal/receipts; raw/normalized/schema/quality/quarantine persist. Không cần proposal để trả pair API.
- [ ] Chạy GREEN + equivalence baseline cho candidates/score/decision; checksum/dependency tests và DB QA real; error không mark DATA_READY sớm.
- [ ] Ghi profile tương thích với legacy/stream/resume; update handoff, lưu measured stages.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_stages.py backend/tests/test_mdm/test_decision_publication.py backend/tests/test_mdm/test_stage_runner.py backend/tests/test_mdm/test_checkpoint_resume.py`; `test_decide_does_not_call_cluster_all_profiles`, `test_decisions_visible_without_proposals`, `test_prepare_profile_survives_restart`, `test_partial_decision_generation_not_served`.

**Prompt — session S02:**

```text
Thực hiện riêng S02 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md, đọc handoff S00–S01 và phần contract. Tách PREPARE, BUILD/SCORE/DECIDE, persist profile và publish cặp khỏi CLUSTER trong mọi runtime path được hỗ trợ, gồm legacy compute, large StageRunner, streaming và staged/resume. Upload chỉ prepare; start-decide chỉ tới DECIDE rồi dừng DECIDE_REVIEW. Không đổi matcher/threshold. Viết regression chứng minh build_proposals không được gọi, pairs/evidence/Group/SubGroup đọc được khi không có proposals, manifest không đầy đủ không phục vụ. Chạy RED/GREEN và DB QA integration, giữ checkpoint/fencing. Dùng superpowers:executing-plans; ghi evidence/handoff S02/SESSION_STATUS.md, không implement cluster hoặc FE trong session này.
```

### S03 — Review cặp, bulk, finalize và reopen

**Files:** tạo `workflow_review.py`; sửa `resolution_pairs.py`, `decision_semantics.py`, pair categories/items adapters, workflow API/service/auth capability mapping; tests `test_workflow_review.py`, `test_workflow_finalize.py`, `test_workflow_bulk.py`.

**Interfaces:** `decide_pair(db, run_id, pair_id, decision, expected_pair_revision, request_id, actor) -> PairDecisionRead`; `submit_bulk_decisions(...) -> OperationRead`; `finalize_decisions(db, run_id, expected_decision_revision, expected_workflow_revision, request_id, actor) -> OperationRead`; `reopen_decisions(...) -> WorkflowRead`.

- [ ] Viết RED cho decisions không tạo proposal/golden, eligibility không theo group label, deferred blocker, Group D release contract, NON_MATCH khác KEEP_SEPARATE.
- [ ] Implement mutation lock/revision/audit/idempotency, all-pair categories/filters và counts theo primary partition; legacy pending endpoint giữ adapter.
- [ ] Implement bulk qua frozen filter + expected revision; batch bounded, partial result report, không self approve conflict/hard constraint, completion chặn khi bulk chưa xong.
- [ ] Implement finalize atomically save immutable snapshot và enqueue CLUSTER; double submit trả cùng operation, stale trả 409. Snapshot có auto-resolved state, không buộc mọi user_decision đã sửa.
- [ ] Implement reopen có reason, giữ snapshot/history và invalidate derived revision; cluster queued/running từ chối review mutation; không sửa kết quả active âm thầm.
- [ ] Chạy targeted RED/GREEN và PG concurrent finalize/review với hai connection; test owner/role/CSRF, không auto chạy CLUSTER trong test review.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_review.py backend/tests/test_mdm/test_workflow_finalize.py backend/tests/test_mdm/test_workflow_bulk.py`; `test_review_has_no_cluster_side_effect`, `test_deferred_blocks_finalize`, `test_finalize_replay_and_stale_revision`, `test_bulk_checks_eligibility_each_pair`, `test_keep_separate_is_not_cannot_link`.

**Prompt — session S03:**

```text
Thực hiện riêng S03 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S01–S02 và Global Constraints. Implement pair review/all categories/bulk decisions/finalize/reopen theo API đích. Quyết định chỉ persist cạnh/evidence/audit, không tạo proposal/golden. Phân biệt MERGE, KEEP_SEPARATE, NON_MATCH, DEFER và semantics legacy; hard constraints/quyền kiểm tra server, Group D không tự đổi policy. Finalize lưu decision snapshot và enqueue CLUSTER atomic, revision/request_id chống stale/replay; bulk dùng filter snapshot và báo applied/blocked/stale. Chứng minh bằng targeted tests và concurrency PostgreSQL QA thật. Dùng superpowers:executing-plans, ghi evidence/handoff S03/SESSION_STATUS.md; chưa triển khai thuật toán cluster hoặc FE.
```

### S04 — CLUSTER sau chốt, conflict và versioned publication

**Files:** tạo `workflow_clusters.py`, `cluster_publication.py`; sửa worker/jobs/service, `clustering.py`, guards, generation/persistence adapters; tests `test_workflow_clusters.py`, `test_cluster_publication.py`.

**Interfaces:** `run_cluster(operation_id, factory, progress) -> None`; `publish_clusters(db, run_id, operation_id, cluster_generation) -> ClusterPublicationRead`; cluster list/detail/conflict APIs chỉ active eligible revision; cluster output chưa gọi golden tự động cũ trong `build_proposals` path v2.

- [ ] Viết RED cho job từ decision snapshot, accepted/missing edge guards, A–B–C bridge/cannot-link, singleton coverage, deterministic order và no early golden.
- [ ] Tách membership clustering khỏi survivorship, consume decisions/negative edges bất biến, guard mọi union. Không ép accepted cạnh vi phạm thành cluster.
- [ ] Lưu conflict IDs/detail, quay DECIDE_REVIEW đúng reason và không công bố bộ nhóm cuối nếu còn conflict; snapshot thử giữ riêng phục vụ audit.
- [ ] Publish membership/generation atomic, unique active membership, source/config hashes khớp; obsolete attempt bị fence. Retry seal/promotion không nhân nhóm/audit.
- [ ] Reopen/recompute giữ lịch sử; invalidate lựa chọn nguồn không còn member, chỉ carry manual overrides có thể xác minh hợp lệ, cần re-confirm sau thay membership.
- [ ] Chạy GREEN và cluster baseline regression; PG fault đúng trước/sau commit, reordered candidates/batches output hash ổn định.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_clusters.py backend/tests/test_mdm/test_cluster_publication.py backend/tests/test_mdm/test_cluster_determinism.py backend/tests/test_mdm/test_safe_clustering.py`; `test_bridge_cannot_link_returns_conflict`, `test_cluster_uses_frozen_decision_revision`, `test_all_records_have_one_active_membership`, `test_old_attempt_cannot_publish`, `test_cluster_does_not_materialize_golden_early`.

**Prompt — session S04:**

```text
Thực hiện riêng S04 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S02–S03 và contract cluster/versioning. Implement CLUSTER worker từ decision snapshot đã finalize, tuyệt đối không gọi từ upload/GET/tab/start-decide. Tách tạo membership khỏi build_golden hiện đang gắn trong build_proposals. Giữ deterministic guards/negative constraints/source/type limits, xử lý A–B–C conflict quay lại Decide, singleton coverage và phase-specific publication atomic/fenced. Reopen/recompute không dùng insert-only legacy persistence để giả cập nhật; preserve lịch sử và human state hợp lệ. Test RED/GREEN, PG concurrency/commit retry thật và baseline safety. Dùng superpowers:executing-plans; ghi evidence/handoff S04/SESSION_STATUS.md.
```

### S05 — Golden BE, registry trường và luật survivorship

**Files:** tạo `field_registry.py`, `golden_service.py`; sửa `golden.py`, `golden_v2.py`, policy config, workflow API/contracts/persistence; tests `test_workflow_golden.py`, `test_golden_field_registry.py`.

**Interfaces:** `build_golden_drafts(db, run_id, cluster_revision, rule_version) -> None`; `update_golden(db, run_id, cluster_id, selections, expected_cluster_revision, expected_golden_revision, request_id, actor) -> GoldenRead`; auto-select, confirm, confirm-eligible và finalize-golden như 1.5. GoldenService là nguồn lựa chọn chính thức cho FE/export.

- [ ] RED cho six canonical + extra mapped fields, singleton, all-null optional, invalid/placeholder, multivalue và structured address.
- [ ] Implement typed field registry shared DTO; preserve extra input fields với policy rõ. Source choice chỉ nhận source member và observation hợp lệ; manual override có audit/provenance và validation kiểu.
- [ ] Implement luật theo field, timestamp nguồn thật/tie-break; golden draft generation sau cluster success; không thay matcher/cluster algorithm.
- [ ] Implement persist/auto-select giữ manual mặc định, reset minh thị, confirm và bulk confirm-eligible; thay giá trị bỏ confirmation. Finalize lock revisions/no unresolved blockers.
- [ ] Test RED/GREEN và PG reload/new client/restart; conflict override khi merged membership không bị overwrite âm thầm; nullable validated đúng.
- [ ] Document rule versions/example reasons và GET/PUT response để S08 dùng; audit/source choice liên kết đầy đủ.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_golden.py backend/tests/test_mdm/test_golden_field_registry.py backend/tests/test_mdm/test_golden_and_benchmark.py`; `test_manual_selection_survives_new_client`, `test_auto_select_preserves_manual`, `test_invalid_source_not_selected`, `test_optional_null_can_confirm`, `test_source_choice_outside_cluster_422`, `test_finalization_requires_current_confirmations`.

**Prompt — session S05:**

```text
Thực hiện riêng S05 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S04 và field/golden contract. Implement BE golden drafts, field registry gồm sáu canonical và mapped extra fields, lựa chọn source/manual trên server, survivorship theo trường, provenance/alternatives, auto-select giữ manual, reset minh thị, confirm từng nhóm/bulk đủ điều kiện và finalize. Singleton có golden; null optional hợp lệ; structured address không tạo tổ hợp sai; source_updated_at phải thật. Mọi mutation kiểm tra ownership/capability/revisions/audit. Chạy RED/GREEN và persistence reload/restart PG QA; không dùng LLM tạo giá trị golden. Dùng superpowers:executing-plans; ghi evidence/handoff S05/SESSION_STATUS.md, bàn giao DTO cho FE.
```

### S06 — FE Tổng quan, upload và session layout

**Files:** sửa `app/app/page.tsx`, AppShell/Workspace/SessionsHub/Intake, api/contracts/sessionView; tạo routes `app/app/uploads/[uploadSessionId]/page.tsx`, `app/app/sessions/[runId]/layout.tsx` và bốn page, `SessionWorkspace.tsx`, `SessionTabs.tsx`; tests `session-navigation.test.tsx`, `session-hub.test.tsx`.

**Interfaces:** FE `getWorkflow/getDataProfile/getOperation`; URLs ở 1.1; backend allowed_actions quyết định nút, local state không quyết định eligibility. Adapter chuyển URL cũ `?run=&view=` sang route phù hợp bằng replace, giữ deep links history/audit.

- [ ] RED cho `/app` chỉ hub, không restore redirect; upload route/resume/commit, old session opening không gọi start.
- [ ] Đọc Next guide liên quan trong node_modules theo AGENTS; implement routes/header/bốn tab và adapter navigation cũ.
- [ ] Move history thành hub page dùng server pagination/search/rename/archive; upload input errors/progress/resume và chuyển committed run vào data tab.
- [ ] Workflow polling/WS có operation/stage keys, reconnect/abort/race khi chuyển run; counts/state đơn vị đúng, không fake % hay stale success.
- [ ] Chạy Vitest targeted, `npm run lint`, `npm run build`; browser kiểm tra desktop/mobile, keyboard tabs/focus và Back/Forward/reload.
- [ ] Lưu screenshot/layout routes và handoff S06; placeholder tab thể hiện readiness thật, không gọi pipeline ngoài user action.

**Tests:** `hub_does_not_restore_last_run`, `opening_history_does_not_start_compute`, `upload_resume_opens_committed_run`, `stale_workflow_response_cannot_replace_other_run`, `legacy_deep_link_adapter`.

**Prompt — session S06:**

```text
Thực hiện riêng S06 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S01–S02, FE AGENTS.md và Next guide đang cài. Sửa FE sau đăng nhập: /app có upload và lịch sử phiên; upload có route/resume riêng rồi vào Tổng quan phiên; session layout có bốn tab/URL/header/readiness dựa WorkflowRead. Không tự restore redirect hoặc start pipeline khi mở phiên cũ; chuyển deep link query cũ hợp lệ. Giữ login/register và auth, dùng API server pagination, chống race khi đổi phiên/poll/WS. Test navigation/permissions Vitest, typecheck/build và browser layout/responsive/keyboard; ghi screenshot/evidence/handoff S06/SESSION_STATUS.md. Dùng superpowers:executing-plans; chưa xây detail review/golden của S07/S08.
```

### S07 — FE Tổng quan dữ liệu và phân loại cặp

**Files:** tạo `DataOverview.tsx`, `DecisionWorkspace.tsx`; refactor/tận dụng PairReviewFlow/resolutionFields, api/contracts; tests `data-overview.test.tsx`, `decision-workspace.test.tsx`; e2e mocked riêng giữ nhãn rõ.

**Interfaces:** APIs S02/S03; group/subgroup/state filter, server cursor; action auto means bulk decision; explanation adapter có state `loading/success/unavailable/error`, không ghi model confidence giả.

- [ ] RED cho profile raw/normalized/quarantine, all-pair categories/status counts, deferred blocker, denied Group D/hard conflict và no cluster before finish.
- [ ] Implement data tab đủ profile/schema/quality; start-decide có button/202 state, show retries không tự chạy lại.
- [ ] Implement comparison table field registry, evidence/raw-normalized, filters, individual four decisions, reason và revision error 409/reload.
- [ ] Implement bulk preview/count/scope + async results applied/blocked/stale, completion summary và finalize button; empty/busy/error hợp lệ, bỏ tên “auto gộp” gây hiểu sai.
- [ ] Implement reopen minh thị và hiển thị stale derived results; direct URL cluster vẫn locked nếu backend chưa ready.
- [ ] Chạy targeted Vitest/typecheck/build và browser scenario bằng API QA thật; audit confirm frontend action không gọi build cluster sớm.

**Tests:** `group_is_pair_classification_not_cluster`, `bulk_applies_server_eligible_only`, `pending_blocks_finalize`, `stale_pair_revision_requires_refresh`, `data_profile_counts_invalid_rows`, `switching_tabs_never_starts_cluster`.

**Prompt — session S07:**

```text
Thực hiện riêng S07 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S02/S03/S06 và FE AGENTS. Xây tab Tổng quan dữ liệu và Phân loại các cặp bản ghi: profile raw/normalized/schema/lỗi, Group/SubGroup/all statuses, so sánh evidence, MERGE/KEEP_SEPARATE/NON_MATCH/DEFER, bulk eligible với preview/results và hoàn tất Decide. Dùng revision và allowed_actions server; nhãn auto là áp dụng quyết định, không tạo cluster ngay. Tích hợp explanation UI vào API hiện có để S10 chứng minh live sau; không fake success. Test Vitest/build và browser API QA thật cho finalize/blockers/reload/permissions. Dùng superpowers:executing-plans, ghi evidence/handoff S07/SESSION_STATUS.md.
```

### S08 — FE cluster editor và Golden record

**Files:** tạo `ClusterWorkspace.tsx`, `GoldenFieldEditor.tsx`, `GoldenRecords.tsx`; tách phần cần dùng khỏi ResolutionFlow; sửa api/contracts/resolutionChoices; tests `cluster-workspace.test.tsx`, `golden-records.test.tsx`.

**Interfaces:** list/detail cluster, selections/auto/confirm/finalize S05; choices lưu server, draft cache chỉ hỗ trợ phục hồi và gắn user/run/member/revision, không submit localStorage cũ làm canonical.

- [ ] RED cho locked tab/no early count, cluster detail/source field choices, six+extra fields, auto/manual and stale 409.
- [ ] Implement paginated clusters/singletons/detail/conflicts, field editor có sources/alternatives/reason, select source/manual với null/multivalue rõ.
- [ ] Implement server save acknowledgment, auto-select/reset/confirm/bulk-confirm, progress dirty/stale, khôi phục new browser đọc server.
- [ ] Implement golden list draft/confirmed và finalization readiness; cuối cùng preview/export/history chuyển sang tiện ích kết quả; không gọi xuất khi chỉ xem tab.
- [ ] Verify reopen/membership change không hiện draft nguồn không còn member; preserve source provenance chính xác sau server reload.
- [ ] Run Vitest/typecheck/build; visual QA table overflow/mobile/keyboard/focus và error/empty/loading; handoff screenshots.

**Tests:** `server_saved_choice_visible_after_storage_clear`, `auto_preserves_manual_and_requires_confirmation`, `all_schema_fields_render`, `locked_clusters_have_no_fake_results`, `golden_final_requires_confirmed_revision`, `reopen_marks_derived_result_stale`.

**Prompt — session S08:**

```text
Thực hiện riêng S08 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S04/S05/S07 và FE AGENTS. Implement tab Các nhóm bản ghi và Golden record: chỉ hiện cluster hợp lệ sau finalize/compute, list/detail gồm singleton, chọn từng field từ registry/provenance, manual override, auto-select/reset, confirm từng/bulk đủ điều kiện và finalization. Lưu lựa chọn server có revision và thông báo save thật; localStorage chỉ draft, không dùng dữ liệu cũ như lựa chọn chính thức. Golden draft/confirmed phân biệt rõ, reload/máy mới giữ choices; reopen làm kết quả stale có lý do. Chạy Vitest/typecheck/build và browser visual QA desktop/mobile/focus. Dùng superpowers:executing-plans; ghi evidence/handoff S08/SESSION_STATUS.md.
```

### S09 — Export, lịch sử và tất cả chức năng cũ

**Files:** sửa preview/export/items/split/import/persistence/API adapters và FE result utilities; tests `test_workflow_exports.py`, `test_workflow_legacy.py`, `test_workflow_split.py`; tận dụng auth/rbac/audit/policy existing tests.

**Interfaces:** preview pinned `decision_revision/cluster_revision/golden_revision/result_hash`, execute idempotent request, download immutable bytes. Existing entity merge/split effects chỉ xảy ra ở action đã định nghĩa; xác nhận golden không tự thực hiện external write.

- [ ] RED cho export cover tất cả accepted source records, golden confirmed/null fields, no duplicate/lost member, CSV formula/encoding, stale preview 409.
- [ ] Align export với server choices/finalized version, history/comparison/source download/audit; không exportChoices localStorage thành canonical.
- [ ] Legacy run v1 read/preview snapshot qua adapter, no implicit migration/rerun; lịch sử bytes/hash unchanged. Nếu muốn sửa run cũ, tạo version2 run có input được phép và lineage.
- [ ] Selective split/unmerge là compensating operation tạo negative links/revision/new result và parent snapshot, không pointer rollback; old export vẫn nguyên.
- [ ] Regression auth/register/default VIEWER, login/logout/rate limit/CSRF/owner; STEWARD/SENIOR/POLICY_ADMIN/AUDITOR thực đúng capability; policy simulation không sửa active pinned policy.
- [ ] Chạy unit/integration API/PG và FE related tests; inventory mọi mounted public route có mapping coverage và lỗi cụ thể.

**Tests/command:** runner `--paths backend/tests/test_mdm/test_workflow_exports.py backend/tests/test_mdm/test_workflow_legacy.py backend/tests/test_mdm/test_workflow_split.py backend/tests/test_mdm/test_rbac.py backend/tests/test_mdm/test_audit.py backend/tests/test_mdm/test_policy_engine.py`; `test_export_is_snapshot_of_server_choices`, `test_stale_preview_409`, `test_split_preserves_parent_export`, `test_legacy_run_and_snapshot_read_unchanged`.

**Prompt — session S09:**

```text
Thực hiện riêng S09 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S04/S05/S08. Hoàn chỉnh export preview/execute/download/source/history/comparison từ golden lưu server và finalized revision, coverage singleton/accepted records, immutable snapshot/replay, CSV safe. Duy trì phiên cũ không rerun và selective split/unmerge có negative links/parent history, audit và policy admin/simulation. Kiểm tra auth/login/logout/default Viewer/role/owner/CSRF và mọi mounted public route còn hỗ trợ. Chạy targeted unit+PG API+FE tests, không xóa chức năng cũ để làm luồng mới pass. Dùng superpowers:executing-plans; ghi coverage matrix/evidence/handoff S09/SESSION_STATUS.md.
```

### S10 — LLM: wiring mới, policy và request thật

**Files:** sửa `llm/gateway.py`, `policy.py`, `explanation.py`, `pipeline/semantic_address.py`, pair publication/audit/API; tạo live fixtures/harness `qa_workflow_llm.py`; tests `backend/tests/test_llm/test_workflow_llm.py`.

**Interfaces:** annotation sidecar gắn immutable DECIDE baseline; explanation UI gọi mounted API; policy context cho `DECIDE_REVIEW/GOLDEN_REVIEW` đúng purpose/capability, không yêu cầu legacy COMPLETED sai mốc. Campaign accounting shared enforced before dispatch.

- [x] RED cho annotation trước cluster giữ baseline và `eligible_for_auto_merge=false`; hash/review freshness invalidation, privacy allowlist, schema invalid/output truncated, budgets/cache/fallback.
- [x] Implement wiring/policy state hợp workflow, gateway/cache/request audit; payload tối thiểu và không có raw PII synthetic ngoài purpose; annotate không đổi routing/decision/auto permission.
- [x] Build fixture chắc chắn đủ eligibility cho ADDRESS_COMPARE (không chỉ bật LLM): test name/contact anchor và weak-address state/range. Một explanation phải khởi phát từ UI/app route thật.
- [x] Live Gemini semantic + explanation trong QA riêng qua app worker/API; ghi provider/model/prompt/schema/request/usage/cost/status. Validate annotation phục vụ FE trước cluster và explanation visible sau reload.
- [x] Test cache hit không outbound thêm; DB/env kill switch, cap, quota/cooldown, stale, invalid schema và timeout bằng deterministic fault transport. Live OpenRouter success nếu backup release-enabled, không gây 429 thật hàng loạt.
- [x] Điều tra output 512/full SHA256 batch và wall-time policy hash 100k theo checkpoint; chọn output cap/batch dựa schema success+cost evidence, không chỉ tăng mù. Run negative regression và record limited live findings.

**PASS live:** có tối thiểu một semantic và một explanation `SUCCESS` thật; request_id ứng dụng liên kết annotation/response UI; baseline hash không đổi; còn trong campaign budget. Cache/template/không có eligible pair đều không thay điều kiện này. Provider/model hiện không truy cập được → BLOCKED live, không giả kết quả.

**Prompt — session S10:**

```text
Thực hiện riêng S10 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S03/S07/S09, LLM contract/budget và checkpoint 100k cũ. Tôi muốn test LLM thật qua gateway của ứng dụng trên dữ liệu synthetic QA, gồm semantic ADDRESS_COMPARE đủ eligibility và EXPLANATION từ màn hình review. Kiểm tra credentials presence an toàn, provider/model/retention/price/DB policy hợp lệ; không bypass gateway hay giả retention approval. Áp dụng campaign smoke cap tối đa 20 outbound attempts/100k tokens/1 USD reservation dùng chung S10/S12; tính retry/fallback. Wire annotation trước cluster không đổi decision/auto permission, preserve freshness/audit/cache; kiểm tra output schema/full SHA256 và bottleneck policy hash. Test cache/kill-switch/budget/429-cooldown/timeout/failure bằng injection; nếu OpenRouter được release-enabled, chứng minh một backup request thật. Ghi usage/cost/evidence đã redact và UI tiêu thụ thật; không coi mock/cache hit là live PASS. Dùng superpowers:executing-plans; handoff S10/SESSION_STATUS.md.
```

### S11 — R2 thật: browser upload, checkpoints và snapshot

**Files:** sửa upload_sessions/mdm_uploads/import jobs, R2/object store/artifacts/checkpoints adapters, config; tạo `docker-compose.workflow.live.yml`, `qa_workflow_storage.py`; tests `test_workflow_storage.py`.

**Interfaces:** browser/server endpoint đúng, QA-owned prefix enforcement hoặc dedicated bucket; write/read receipts+manifest checksums dùng actual R2. New live overlay thay hoàn toàn env Moto cả migrate/backend/worker/presign; không chạy bucket create/list với credential production.

- [ ] RED cho endpoint nhầm bucket path, prefix escape, unauthorized part/run, checksum mismatch, expired URL/resign/resume và incomplete manifest.
- [ ] Implement/verify storage adapter và direct upload UI, R2 CORS origin exact/headers exposed, multipart/part completion/sweep owned only; không silently fallback proxy để ghi PASS direct.
- [ ] Chạy browser PUT thật → validate/commit/prepare/decide/pause → finalize/cluster/golden/export; đối soát source/stage/result objects/checksum bằng authenticated backend, không cần public bucket.
- [ ] Restart worker/API và remove chỉ owned local scratch copy để chứng minh R2 durability/resume; download snapshot đúng bytes từ browser sau restart.
- [ ] Fault missing/corrupt artefact bằng objects QA campaign, không sửa historical originals; fail closed/repair via retry, object inventory gồm export manifests/audit sidecar/checkpoints.
- [ ] Record cloud network/error/cost metadata, reconcile owned inventory không orphan; chưa purge evidence cần S13 restore.

**PASS live:** actual Cloudflare R2 endpoint và objects tồn tại/checksum đúng; presigned PUT từ browser; input/output survive runtime restart; downloads match pinned snapshot. Moto/local file fallback không thay PASS.

**Prompt — session S11:**

```text
Thực hiện riêng S11 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S02/S04/S09 và QA/R2 scope. Tôi muốn kiểm thử Cloudflare R2 thật từ UI direct upload, validation/commit, PREPARE/DECIDE checkpoint, cluster/golden/export và mở lại sau restart. Dùng bucket QA riêng hoặc prefix enforced, không in keys/signed URL, không sửa .env hiện tại và không tác động bucket/data khác. Tạo live Compose overlay nhất quán API/worker/migration/presign; validate endpoint không chứa bucket path và CORS đúng. Test timeout/resign/checksum/missing object/prefix escape, manifest integrity và browser download hash. Chứng minh không fallback local/Moto mà vẫn báo R2 PASS; reconcile inventory gồm mọi manifest/sidecar/history. Dùng superpowers:executing-plans; ghi evidence/handoff S11/SESSION_STATUS.md, giữ objects cho restore tests.
```

### S12 — Docker packaged + browser toàn tính năng thật

**Files:** sửa Dockerfiles/Compose workflow/worker health, portable Playwright; tạo `playwright.workflow.config.ts`, `e2e/workflow-live.spec.ts`, `qa_workflow_acceptance.py`; sửa app nếu phát hiện failure có regression.

**Interfaces:** production build từ source candidate, không bind mount checkout; explicit migration service trước API/worker, `/health`/`/ready`, worker heartbeat health riêng. Playwright Chromium portable/optional Edge local, QA baseURL env, không tự spin dev server cho live tests.

- [x] Build fresh BE/FE images, start QA/live overlay đúng manifest; assert installed module hash/image digest/migration head/selected DB. Không chỉ dùng host .venv để chứng nhận container.
- [x] Add/run browser E2E không route mocks/service-worker fake API: auth → hub → upload R2 → data → start-decide → categories/review/bulk → finish → cluster → manual/auto/confirm golden → final/export/history.
- [x] Verify backend database/object effects mỗi action; clear browser localStorage/new context login mở phiên để chứng minh server persistence; keep history immutable.
- [ ] Browser test conflict/reopen/recompute, stale two tabs, eligible semantic/explanation live, split/audit/policy roles và unauthorized attempts; account roles setup bằng QA CLI/fixture không đổi user production.
- [ ] Run desktop/mobile/keyboard, visual inspect các screenshot chủ chốt, table overflow/error/busy states; WS/poll disconnect/reconnect vẫn đúng phiên.
- [x] Full backend/FE required checks + live evidence summary; fix phát hiện có repro và rerun affected tests, không broad rerun paid calls vô ích.

**Mười hai scenario bắt buộc:** đăng nhập/quyền; hub/history; upload resume/invalid; data profile; Group/SubGroup/bulk; premature-cluster denied; bridge conflict; golden manual+auto+extra fields; reload/new browser; export/history/hash; split/audit/policy; real LLM semantic+explanation. Ghi scenario riêng nếu cần để test đơn vị tính/multivalue.

**Prompt — session S12:**

```text
Thực hiện riêng S12 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S06–S11 và campaign budget. Tôi muốn chạy ứng dụng Docker production-build thật (FE/API/worker/PG/R2 live), mở browser và test mọi tính năng đã liệt kê, không mock API hay dùng Next dev để chứng nhận. Chuẩn hóa Playwright portable/baseURL, migration startup và worker healthcheck. Test từ login/hub/upload đến chốt/export/mở lại, cả Group/SubGroup/bulk/conflict/reopen/choices auto/manual/extra fields/singleton, LLM semantic+explanation đủ eligibility, roles/audit/policy/split/history. Verify DB/R2 effects/checksum/server persistence bằng browser mới; budgets LLM dùng chung S10 không reset. Chạy required checks, fix lỗi có regression và lưu screenshots/traces/logs/digests. Dùng superpowers:executing-plans; handoff S12/SESSION_STATUS.md, không tuyên bố deploy-ready chỉ vì health xanh.
```

### S13 — Recovery, backup/restore, observability và soak

**Files:** sửa job_queue/worker/operation/checkpoint/publication/health, monitoring; tạo `qa_workflow_recovery.py`, tests `test_workflow_recovery.py`, operational runbook/recovery matrix.

**Interfaces:** operation-stage attempts frozen, heartbeat/readiness/metrics có labels run/operation/attempt; backup manifest DB+R2 references; restore vào DB/bucket mới, không ghi đè resource gốc.

- [ ] RED deterministic cho worker chết ở PREPARE/DECIDE/between finalize-enqueue/CLUSTER/publish/export, expired owner cannot commit, ambiguous commit ack replay.
- [ ] Run Docker QA fault drill kill/restart đúng owned worker; API restart giữa response, DB outage, R2 unavailable/missing, LLM timeout/quota. Không tạo duplicate audit/business effects/paid dispatch khi retry không chắc.
- [ ] Prove stage resume đúng input/config/review hash; user waiting review không giữ lease/worker slot; restart toàn owned stack giữ business/history hashes.
- [ ] Backup PG + copy toàn object reference/checksum sang QA clone; restore, login/read profile/pairs/cluster/golden/history/download bằng packaged app; đo RPO/RTO thực.
- [ ] Add metrics/alerts queue age/worker stale/failed stage/storage errors/LLM budget/publication mismatch; làm alert thật phát tín hiệu tới sink QA do người dùng cấu hình, không tự gửi tin người khác.
- [ ] Soak/repeated sessions bounded: queue/progress/DB pool/RSS/disk/cost/object inventory không tăng vô hạn; nguồn leak/fault sửa có regression, ghi giới hạn đo.

**Tests:** `test_expired_attempt_fenced_each_operation`, `test_commit_ack_loss_replay_no_duplicate`, `test_review_pause_releases_worker_slot`, `test_restore_matches_business_and_export_hashes`, `test_missing_artifact_blocks_publication`, `test_heartbeat_alert_emitted`.

**Prompt — session S13:**

```text
Thực hiện riêng S13 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S12 và owned resource manifest. Test/sửa recovery thật trên Docker QA: kill worker từng stage, expire lease, restart API/worker, DB/R2 outage, commit ack loss, LLM fault, replay/export double-submit. Giữ fencing/checkpoint hashes/human state, không duplicate business effect hoặc paid requests; dừng chờ review không chiếm worker. Backup PG+toàn R2 references rồi restore vào tài nguyên QA mới và xác minh qua app/browser lịch sử/download/checksum. Bổ sung metrics/worker health/alerts và chứng minh alert tới sink QA được cấu hình; không gửi tin cho người khác. Chạy bounded soak, đo RSS/disk/queue/DB pool/cost và giữ evidence. Không fault production, không down -v/prune. Dùng superpowers:executing-plans; handoff S13/SESSION_STATUS.md/runbook.
```

### S14 — Chất lượng và hiệu năng, 100k toàn luồng

**Files:** tận dụng benchmark standalone/datasets manifests, `production_benchmark.py`/verifier/report; tạo workflow benchmark harness và quality/capacity report mới. Sửa algorithm/ingestion chỉ khi có root cause + characterization/regression; threshold/policy changes cần decision record.

**Interfaces:** input immutable manifest/independent entity+field gold; workflow benchmark tới full pair publication, manual-policy simulation được đánh dấu riêng, cluster/golden/final/export; timing CPU stage tách wall chờ review, toàn source ingest phân biệt quarantine/accepted.

- [ ] Freeze datasets/holdout/truth hashes trước chạy; assert truth/label columns không vào matcher/LLM input; không lấy golden replay cùng thuật toán làm ground truth độc lập.
- [ ] Test full original TC5 input và support contract: decode/mandatory errors báo coverage/quarantine đầy đủ, không silently project/drop 521 rows rồi ghi full PASS. Excel chỉ quảng bá nếu native importer có test; không gọi CSV projection là native Excel.
- [ ] Benchmark 1k/10k trước, rồi full source của TC1/2/3/4/5/6/8/13 thuộc suite 100k nếu manifest thực tế khớp; không giả file nào cũng đúng 100.000 rows. Không chạy 1M trong kế hoạch này.
- [ ] Đo candidates recall/precision, auto merge precision/recall, pair decisions, cluster B-cubed/pairwise, golden field accuracy, JUNK merges, review workload theo strata và independent gold.
- [ ] Đo upload/prepare/build/score/decide/decision publication/review/finalize/cluster/golden/export riêng; PG SQL/pool, CPU/RSS/disk/R2 bytes, API list P95, two-worker concurrency, queue throughput. Review synthetic auto-driver chỉ để đo compute, không chứng minh human review capacity.
- [ ] A/B LLM off/on cùng source/profile/pinned versions, live đủ eligible và budget riêng đã chốt; đọc provider usage/errors/accounting, không rerun checkpoint paused roots. Report includes publish/export/UI samples mới.
- [ ] Sửa source defect/harness regression rồi rerun cases ảnh hưởng trong root mới; đối chiếu quality/SLO targets ở release profile và đánh dấu gate chưa đạt, không sửa targets sau nhìn kết quả.

**Chú ý:** Chứng cứ 100k trước persist cũ chỉ dùng tham khảo. Phải kiểm tra publish latency/DB cardinality/query pagination/history thực sự, không chỉ tốc độ matching. SLO/quality target chưa được người dùng chốt → metrics vẫn chạy, release R10 chưa PASS.

**Prompt — session S14:**

```text
Thực hiện riêng S14 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S12/S13, release-profile và benchmark checkpoint cũ nhưng không resume/overwrite roots đang paused. Tôi muốn benchmark chất lượng+capacity của workflow mới tới ghi DB/R2, cluster, golden, finalize và export đầy đủ, không dừng trước persist. Freeze independent gold/holdout, đo coverage original TC5/decode/quarantine, recall hard cases/JUNK/bridge/field accuracy và workload; không dùng chính thuật toán golden replay làm truth. Chạy 1k/10k rồi suite full 100k TC1/2/3/4/5/6/8/13 theo manifest, không chạy 1M. A/B live LLM off/on chỉ sau budget riêng đã chốt, cùng data/profile và đủ eligible; giữ mọi lỗi/schema/cost. Đo từng stage/publish/export/API P95/memory/disk/PG/R2/two workers, tách thời gian người duyệt. Không hạ guard/targets hoặc drop rows để pass. Dùng superpowers:executing-plans; ghi quality/capacity evidence/handoff S14/SESSION_STATUS.md và blockers cụ thể.
```

### S15 — CI, release images và HTTPS staging

**Files:** sửa `.github/workflows/ci.yml`, Dockerfiles/Compose/ignore/lock strategy/config; tạo staging deployment templates/checklist, `qa_workflow_staging.py`; update runbooks.

**Interfaces:** một release candidate commit+diff snapshot/image digests, dependency locks, migration head, runtime config redacted; same profile QA/staging/prod. Target staging phải do người dùng cung cấp hoặc tài nguyên QA được xác minh; không tự tạo dịch vụ trả phí/public production.

- [ ] Required lint sạch toàn scope thực tế CI; sửa lỗi kế thừa có kiểm chứng, không blanket-ignore/disable rules hoặc deselect tests. Full unit/contract/PG integration/FE typecheck/build/migration single head.
- [ ] Align Node CI với tested Docker version, pin dependencies/reproducible Python lock và image build; portable Playwright trên Linux CI, mocked and live workflows phân biệt. Live jobs gated secret+budget, không chạy paid tests trên PR không tin cậy.
- [ ] Container non-root FE/BE, permissions cho WORK_DIR và tmp, health/readiness riêng worker, resource limits/graceful shutdown/log rotation; thử config hardening bằng runtime, không thêm read-only gây crash.
- [ ] Run dependency/image OS scan bằng công cụ có sẵn, triage result có impact; secret/image layer/build context check .env/private_state không bị đóng gói.
- [ ] Deploy candidate tới staging được cấu hình: HTTPS/domain/reverse proxy/cookie Secure-SameSite/CSRF/CORS/WS/presign/R2 CORS thật; run browser acceptance subset + eligible live provider canary.
- [ ] Practice migration/backward compatibility/backup and rollback binary trên clone/staging, preserve business snapshots; không downgrade data tùy tiện, forward fix nếu migration không reversible. Alert+restore/on-call/runbook/SLO owner điền thật.
- [ ] Ghi CI run URL/status nếu có remote execution; không publish/push không được yêu cầu. Local CI equivalent PASS không thay remote required check nếu release yêu cầu remote.

**Tests:** Linux Playwright builds; config fail-closed; mixed old/new app schema compatibility; cookies/origin across HTTPS; startup with mounted writable dirs/non-root; rollback preserves data/history. Chưa có hosting target thì chuẩn bị package+runbook nhưng R12 staging BLOCKED, không tự claim GO.

**Prompt — session S15:**

```text
Thực hiện riêng S15 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md; đọc handoff S12–S14/release-profile. Chuẩn hóa CI required lint/tests/build/migrations, Node CI/Docker, dependency locking và Playwright Linux portable; không bỏ lỗi kế thừa hoặc tắt checks để xanh. Build production images không secrets, non-root FE/BE, real worker health, resource/shutdown/log controls và dependency/OS scans có triage. Chuẩn bị và kiểm chứng HTTPS staging trên target QA đã cung cấp, cùng digests/profile: cookies/CSRF/CORS/WS/R2 presign, browser acceptance và LLM canary trong remaining budget. Thực tập backup/migration/binary rollback preserve dữ liệu trên clone/staging. Nếu thiếu target/credentials/budget hỏi đúng phần thiếu, tiếp tục packaging/CI độc lập; không tạo paid hosting hoặc deploy production. Dùng superpowers:executing-plans; ghi evidence/handoff S15/SESSION_STATUS.md/runbooks/CI status thực.
```

### S16 — Nghiệm thu toàn bộ và bàn giao deploy

**Files:** tạo `docs/session-workflow/RELEASE_READINESS.md`, `ACCEPTANCE_MATRIX.csv`, `DEPLOYMENT_HANDOFF.md`, `FINAL_DEFECT_REGISTER.md`; cập nhật status và handoff S16. Chỉ sửa product code khi có failure cụ thể + regression và phải rebuild/retest affected release candidate.

**Interfaces:** audit harness tổng hợp machine evidence theo R01–R12 trên đúng source/images/config; exit nonzero nếu required gate FAIL/BLOCKED/NOT_RUN. Report không ghi GO có điều kiện mập mờ.

- [ ] Đối chiếu mọi yêu cầu người dùng với session/test/evidence, mounted routes và UI thực; kiểm tra evidence campaign budget/IDs không thuộc bản cũ.
- [ ] Chạy fresh final required checks sau thay đổi cuối; chạy golden path production Docker/browser với live R2 + eligible LLM, reload/history; không lặp benchmark/paid calls nếu candidate/path chưa đổi và evidence còn hợp lệ.
- [x] Reconcile DB membership/decision snapshots/golden confirmations/export hashes/R2 inventory/LLM reservations vs attempts/usage; validate machine assertion results không chỉ process exit.
- [ ] Verify regression legacy/auth/role/audit/split/policy, D decision, supported input formats, independent quality/SLO, restore/alerts/staging; unresolved critical/high functional blocker không được GO.
- [ ] Tạo GO/NO-GO table R01–R12 kèm evidence/scope; mỗi fail có reproduction/owner/fix next step, không đóng defect lịch sử chỉ bằng suy luận.
- [ ] Bàn giao exact digests/migration/config names/secrets delivery method/start commands/backup/rollback/monitoring/limits và post-deploy canary. Production cutover cần user chỉ dẫn cuối, không tự execute.

**Prompt — session S16:**

```text
Thực hiện riêng S16 của docs/superpowers/plans/2026-10-11-session-workflow-deployment-plan.vi.md. Đọc toàn bộ SESSION_STATUS, handoff S00–S15, release-profile và evidence manifest; dùng superpowers:executing-plans và verification-before-completion. Audit mọi yêu cầu FE/BE cùng toàn bộ tính năng live: deferred cluster, Group/SubGroup/bulk, server golden choices/all fields, legacy/history/export/split/audit/policy/auth, LLM eligible semantic+explanation, R2 direct/checkpoints/durability, Docker production runtime, recovery/restore/alerts, quality/capacity, CI và HTTPS staging. Chạy fresh required checks và final live golden path đúng candidate/digests/config, reconcile DB/R2/LLM accounting; không coi mock/cache/skip hay evidence cũ là PASS. Fix failure cụ thể có regression rồi rebuild/retest affected scope. Xuất RELEASE_READINESS.md, ACCEPTANCE_MATRIX.csv, FINAL_DEFECT_REGISTER.md và DEPLOYMENT_HANDOFF.md với GO hoặc NO-GO theo R01–R12; mọi BLOCKED/NOT_RUN giữ rõ. Chưa deploy production/push/cutover. Ghi handoff S16 và cuối cùng báo kết quả cùng giới hạn/bước production còn cần chỉ dẫn.
```

## 4. Checklist nghiệm thu cuối cho người dùng

- [ ] Đăng nhập tới Tổng quan có upload + lịch sử, mở phiên cũ không chạy lại.
- [ ] Upload R2 thật có resume/validate/commit, lỗi không làm mất file/phiên hoặc âm thầm bỏ dòng.
- [ ] Tổng quan dữ liệu hiển thị PREPARE, schema/raw/normalized/quality đầy đủ.
- [ ] DECIDE hiển thị all pairs/Group/SubGroup/evidence, auto/bulk đúng eligibility, có manual review.
- [ ] Chuyển tab/GET/upload/start-decide không chạy cluster. Finalize chỉ sau giải quyết blockers.
- [ ] Cluster đúng constraints, bridge conflict không bị gộp sai, singleton và coverage đầy đủ.
- [ ] Golden chọn từng field/auto rule/source/manual/null/multivalue, lưu server và re-confirm đúng revision.
- [ ] Mở trình duyệt mới/máy khác đọc lại choices; chọn nguồn có provenance, không phụ thuộc localStorage.
- [ ] Tab Golden có kết quả cuối, preview/export/download/history immutable đúng hash.
- [ ] Reopen/recompute/split giữ audit/history và không overwrite human state âm thầm.
- [ ] Semantic LLM và explanation thật qua gateway+UI; usage/cost/cache/kill switch/fallback có bằng chứng.
- [ ] R2 lưu checkpoints/result/manifest/export thật, object inventory và restart/restore đúng checksum.
- [ ] Docker build sạch chạy FE/API/worker/PG, explicit migration/readiness/worker heartbeat hoạt động.
- [ ] Auth/Viewer/Steward/Senior/Admin/Auditor, owner/CSRF/origin và Group D đúng policy release.
- [ ] Quality độc lập, ingestion TC5/hard cases/JUNK, 100k full publish/export, SLO/capacity đạt phạm vi quảng bá.
- [ ] CI required checks/portable E2E/image/secret scan đạt, runtime version/image digest khớp.
- [ ] HTTPS staging thật, rollout/rollback/backup/restore/alert delivery đã thực tập.
- [ ] R01–R12 PASS và không còn blocker bắt buộc trước production cutover.

## 5. Những phần cần quyết định khi bắt đầu thực thi

Không chặn việc viết kế hoạch hiện tại. S00 ghi nhận và hỏi người dùng từng phần khi cần, đồng thời tiếp tục task độc lập:

1. **Group D:** giữ riêng trong release đầu, cho single-Steward có phê duyệt, hay cần two-person workflow? Two-person kéo theo sharing/membership riêng, không tự cài legacy four-eyes.
2. **Scale/quality/SLO:** release đầu tối đa bao nhiêu records/file/phiên/người dùng đồng thời; targets precision/recall/field accuracy và latency/review workload. Đề xuất đo 100k không đồng nghĩa đã cam kết hỗ trợ 100k production.
3. **Schema:** release hỗ trợ CSV native hiện có và mapping fields nào; Excel native chỉ bổ sung nếu được chọn và importer được kiểm chứng, không ngầm quảng bá.
4. **LLM:** provider/model/account hợp lệ, synthetic smoke trong cap trên, ngân sách benchmark riêng; policy/retention/cost cho dữ liệu production cần ghi đúng nguồn kiểm chứng.
5. **Staging/deploy:** target, domain, certificate, QA DB/R2 resources, secret delivery, alert sink, recovery/retention owner. Nếu chưa có target, vẫn làm local production build/release package nhưng chưa có staging GO.

**Kết quả mong đợi sau S16:** mã nguồn và release artefact đã kiểm chứng, báo cáo gate có evidence, giới hạn rõ và hướng dẫn deploy cụ thể. Tài liệu/containers chạy được/test unit xanh riêng lẻ không đủ để tuyên bố production-ready.
