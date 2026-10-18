# Hợp đồng P-119 phải bảo toàn

Snapshot trước migration thuật toán, 2026-10-08. Source of truth: route được `masterone.main` mount, `frontend/src/lib/contracts.ts` + `api.ts`, service active và ORM/migrations; không chỉ `mdm/schemas.py` (có legacy DTO).

## API surface thật

Auth dùng cookie/session, mutation CSRF và capabilities; ownership theo run/upload/export, cross-owner không được truy cập. Base frontend: `NEXT_PUBLIC_API_BASE_URL` hoặc host local port 8101; MDM prefix `/api/v1/mdm`.

| Method/path (sau prefix) | Contract cần giữ |
| --- | --- |
| `GET/POST /runs`, `GET/PATCH /runs/{run_id}`, `POST /runs/bulk` | History pagination/cursor, rename/archive/restore/delete semantics, owner; multipart create files |
| `POST /runs/{run_id}/start` (202), `GET /runs/tasks/{task_id}` | task_id/run_id/status; retry/audit, không chạy lại completed hoặc active run |
| `/upload-sessions` create/list/get/rename/delete | draft name/status/total_bytes/files; tối đa 10 file, filename/size validation |
| `PUT /upload-sessions/{id}/files/{file}/parts/{part}` | Binary chunk, `X-Chunk-SHA256`, CSRF; part receipt/hash/byte size, resume/replacement links |
| `POST .../validate`, `POST .../commit` (201) | status/error và `{run_id,status,profile}`; idempotent committed run linkage |
| `GET /runs/{id}/overview`, `/analytics`, `/categories`, `/pair-categories` | Giữ keys, units, grouping, task restoration; analytics chỉ khi COMPLETED |
| `GET /runs/{id}/items/{bucket}` | `bucket=merge,no_merge,review,review_pairs`; total/items, pagination/filters |
| `GET /runs/{id}/items/{bucket}/{item}` | records, field_options, choices, missing_fields, pair evidence, blocked reason |
| `GET /runs/{id}/separate-records` | source records, review_pending, pagination/search |
| `POST /runs/{id}/review-pairs/{pair}/decision` | **Active** body `{decision: merge|no_merge, revision: >=1}`; pair_id, decision, revision, proposal_id, affected_record_count |
| `POST /runs/{id}/review-items/transition` | `{item_ids: nonempty list, decision: merge|no_merge}` -> decision/changed_count/changed_ids |
| `POST /runs/{id}/merge-items/{item}/resolve-conflict` | explicit conflict resolution/rejected_pair_count, actor audit |
| `GET/PUT /runs/{id}/proposals/{proposal}/golden-record` | selected values/source/provenance, permitted field set, override audit; không overwrite bằng replay |
| `POST /runs/{id}/export-readiness` | `{acknowledge_unreviewed?:false, choices?:{}}` -> counts/incomplete_items/blockers/can_export |
| `POST /runs/{id}/previews` | cùng export body -> preview_id/status/eligible_count/review_required_count/separate_record_count/estimated_output_rows/policy_version/expires_at |
| `POST /runs/{id}/previews/{preview}/execute` | **Active** `{execute_request_id: 1..80 chars}` -> CSV; replay cùng ID trả cùng export |
| `POST/GET /runs/{id}/exports`, `GET .../{export}/download`, `/compare` | Snapshot CSV hoặc list summary; immutable historical artifact, parent_export_id, ownership |
| `GET .../exports/{export}/merges`, `GET .../{event}/split-preview`, `POST .../{event}/split` | Split body `{member_id,version,execute_request_id}`; preview version check, export_id/parent_export_id/row_count, idempotency |
| `GET /runs/{id}/source-records.csv`, `/audit` | source export + append audit action/details/actor/links and pagination |
| Explanation/policy/benchmark/entity endpoints | Không xóa dù UI subset dùng ít; capability/PII controls; không thay đổi policy qua tối ưu perf |

Không nhầm active DTO với `ResolutionPairDecision(decision=ACCEPTED|REJECTED|DEFERRED, expected_revision)` hay `ResolutionConfirmRequest(preview_token,idempotency_key)` legacy. Test mới bảo vệ khác biệt này.

Golden PUT active dùng `{fields: dict[str,str]}` (name/phone/email/address/dob/government_id); `{fields:{}}` xóa `_golden_override` và dựng lại base survivorship khi override đã materialize. GET trả proposal_id/status/golden_fields/member_records/steward_override; PUT trả proposal_id/steward_override. Không đổi sang legacy GoldenRecordRequest chứa FieldChoice. Adapter task increment proposal revision tại PUT để invalidate preview; GET/export/merge-event áp dụng saved override với STEWARD_OVERRIDE provenance, ưu tiên hơn submitted source choice cùng field. Retry không xóa override; historical export giữ immutable. Regression và rollout xem [ENGINE_ADAPTER](ENGINE_ADAPTER.md).

## Response DTO và đơn vị

- Run status frontend: UPLOADED, QUEUED, RUNNING, COMPLETED, RETRYABLE_FAILED, FAILED. Task: task_id/run_id/status/phase/processed_count/total_count/error_code/updated_at.
- Overview: run_id/status/phase/counts, record_count/file_count/source_counts/history_count/candidate_count, estimated_entity_count/duplicate_record_count/eligible_merge_group_count, task nullable. Counts auto là proposal, rejected/hard_blocked/groups là pair, separate là source record; không cộng thành một tổng.
- Item: id/bucket/kind/origin/member_record_ids/member_count/pair_count/label + optional score/category/status/revision/can_merge. Giữ kind proposal/pair/review/deferred và origin AUTO/REVIEW.
- Detail: raw record identity/source, field options `{source_record_id,source_system,external_id,value}`, choices/missing fields, evidence state/provenance/normalization tags.
- Analytics: `metric_version`; decisions initial_classification/source_record; conflicts pending_review_pairs/pair; quality valid_assessed_fields. Không đổi mẫu số ngầm.
- Export summary: id/row_count/merged_group_count/pending_review_count/created_at/parent_export_id; audit event id/run_id/actor_user_id/pair_id/proposal_id/action/details/created_at.
- Rule score hiện tại không tự động là probability. Chuyển model cần adapter/version và kiểm chứng, không rename một trường để thay semantics.

SCORE v1 đã tách `rule_score`, `raw_rule_score`, `match_probability` và
`splink_match_weight`, giữ `score`/`match_weight` rule units ở DECIDE/serving.
Full candidate coverage và pinned model/config nằm trong scoring artifacts;
full-snapshot census dùng chung small/large. Splink mode chưa kích hoạt vì thiếu
model calibrated/evaluated phù hợp. Xem [SCORING](SCORING.md); không thêm serving
DB columns hoặc thay decision policy trong task này.

## Review workflow cần bảo toàn, không nhập nghiệp vụ OneMaster ngầm

1. Active pair decision khóa run/pair bằng `with_for_update`, kiểm revision, status COMPLETED, pair đang REVIEW và UNDECIDED/DEFERRED; 404/409/403 theo route/auth.
2. Merge mở rộng qua eligible proposals, kiểm blocked internal pairs/staged conflict, supersede proposals cũ, tạo REVIEW_APPROVED origin REVIEW, increment revisions. No_merge ghi REJECTED và tách proposal bị ảnh hưởng. Open case chỉ bỏ edge đã quyết định, giữ edge khác.
3. Item transition hỗ trợ grouped review và bulk; conflict resolution riêng. `ELIGIBLE={AUTO_READY,REVIEW_APPROVED}`. REVIEW_STAGED_CONFLICT không được export như merge hợp lệ.
4. User decisions lưu UNDECIDED/ACCEPTED/REJECTED/DEFERRED, khác algorithm pair_decision. **Decision task đã tách review veto khỏi cannot-link; pipeline đọc active negative edges và historical explicit NON_MATCH riêng.** Không tự chuyển rejected proposal thành person cannot-link. Corrected person semantics mặc định OFF; xem [DECISIONS](DECISIONS.md).
5. Group D four-eyes có legacy implementation/schema, nhưng active item/pair route và test cho phép một steward approve Group D khi không có hard negative. Không tuyên bố four-eyes được áp dụng toàn bộ; không đổi hành vi này trong migration hiệu năng. Cần product decision nếu muốn thống nhất.
6. Golden có base survivorship + steward source choice/override; browser còn giữ `masterone.choices.v1.<run_id>` với sorted member IDs, đưa choices vào preview/export. Không đổi ID/member identity làm mất draft; draft chưa submit không được giả định đã persist DB.
7. Preview OPEN 15 phút, freezes pair_set_hash từ id/revision/user_decision, proposal revisions, policy_version, choices, actor. Execute kiểm stale/expiry/guard và request ID; EXECUTED replay cùng ID giữ snapshot.
8. Export bao phủ mọi record đúng một lần, proposal disjoint; pending review chỉ xuất riêng khi acknowledge. Merge events giữ membership/pair IDs/golden/guard snapshot. Split tạo parent-child export, negative constraints/audit; lịch sử cũ không bị rewrite.
9. Recompute/retry chỉ thay algorithm-owned generation sau khi kiểm chứng; không xóa human-owned decisions, golden overrides và audit. Việc chuyển IDs/schema cần mapping/backfill rõ ràng.

## Database schema bảo toàn

Runtime PostgreSQL (Neon/local profile); tests phần lớn SQLite. `main.lifespan` auto-upgrade qua `require_current`; **không mở lifespan với DB thật để smoke**. Head hiện tại `0024_job_ownership`; migration graph có merge revisions. Chưa chạy upgrade trên DB thật. Private staging/publication schema và rollback giữ dữ liệu: xem [GENERATIONS](GENERATIONS.md).

| Table / model | Fields, keys và dữ liệu không được mất |
| --- | --- |
| `users`, `auth_sessions`, `auth_login_attempts` | account/password hash/role, session và login history; CSRF/capability không bypass |
| `mdm_resolution_runs` | id/owner/name/archived_at/status/phase/record_count/file_count/candidate_count/insufficient_count/policy_version/timestamps/upload_session_id unique; profile là DTO tạo từ records/files, không phải cột run |
| `mdm_resolution_files` | run/file/hash/row_count; unique(run_id,sha256) |
| `mdm_resolution_upload_sessions/files/parts` | draft owner/status, file positions/expected/received/hash/replacement, unique file part number và receipts; committed linkage |
| `mdm_resolution_records` | id/run/external_id/source/entity_type/raw/normalized/quality/extra/benchmark fields; **unique(run_id,source_system,external_id)** qua migration 0021; original_fields/mapping_provenance/lineage nullable JSON; giữ legacy UUID/JSON |
| `mdm_resolution_tasks` | unique run_id; status/phase/counts/error/timestamps; current attempt_id, retry budget, cancel_requested; xem [WORKERS](WORKERS.md) |
| `mdm_resolution_job_attempts` | task/run/worker IDs, lease/heartbeat/deadline, terminal status/error/timestamps; retained retry history |
| `mdm_resolution_pairs` | PK id; unique(run_id,left_record_id,right_record_id); blocking/evidence/score/pair_decision/reason/triage/group/subgroup/signature/tags/policy/user_decision/revision |
| `mdm_resolution_review_cases` | pair_ids/member_record_ids/category/signature/status/revision; first/second reviewer/decision/timestamps vẫn phải giữ |
| `mdm_resolution_proposals` | member_record_ids/golden_fields/entity_type/status/origin/revision/decision_reason |
| `mdm_resolution_explanations` | unique(run_id,signature_hash), explanation/model/usage/error/status |
| `mdm_resolution_audit_events` | actor FK users, pair/proposal links, action/details/timestamp; không rewrite history |
| `mdm_resolution_exports` | csv_bytes nullable, r2_key nullable, parent_export_id self FK, actor/counts/timestamp |
| `mdm_resolution_previews` | actor/policy/pair_set_hash/proposal_revisions/choices/acknowledgment/status/execute_request_id/export_id/expiry |
| `mdm_resolution_merge_events` | preview/export/proposal/actor links, members/pairs/golden_fields/guard_results snapshot |
| `mdm_resolution_split_events` | unique(run_id,request_id), before/after export/actor/proposal/separated/retained/negative pair IDs |
| `mdm_resolution_entities/members/versions` | entity status/type/version; unique(entity_id,record_id), unique(entity_id,version); golden history/actor |
| `mdm_resolution_negative_edges` | run/endpoints/created_by/reason/active/timestamp; semantics/replay cần kiểm tra đường active trước khi đổi |
| `mdm_resolution_golden_field_values` | unique(entity_id,version,field_name), display/source/provenance |
| `mdm_resolution_stage_results` | unique(run_id,attempt_id,stage_name), success/duration/metrics/output_artifacts/error; model có nhưng runner chưa persist |
| `jobs`, `job_stages` | generic storage job/checkpoint metadata, owner; không tự coi là ResolutionTask |
| `llm_requests`, `llm_semantic_evidence`, `llm_explanations` | gate/audit/cache-linked metadata theo migration; không expose credentials/PII |

Schema đầy đủ có thể kiểm tra từ `Base.metadata` sau import auth/mdm/storage repository và Alembic scripts. Một số table chỉ xuất hiện qua migrations/LLM subsystem; không coi metadata subset là toàn DB. SQLite test không chứng minh PostgreSQL lock/concurrency/COPY behavior.

## CSV và input compatibility

Current parser nhận LINH, CRM, BILL, ERP, LEGACY, sources CRM/ERP/BILL/CSV. Input contract `p119_input_v1` chuyển aliases qua resolver versioned; xem [INPUT_CONTRACT](INPUT_CONTRACT.md) để biết provenance, errors và migration/rollback. Chưa triển khai DB production. Giữ fixture cũ khi thêm schema mới; mapping phải versioned và không làm mất raw/DOB/ID/extra/lineage.

Current export UTF-8 BOM, columns theo thứ tự:

`master_record_id, full_name, phone_number, email_address, raw_address, source_record_ids, source_systems, record_count, resolution_status, review_pending`.

source IDs/systems nối bằng `|`; status MERGED/UNMERGED; review_pending `true`/`false`. DOB/ID golden có thể tồn tại nhưng CSV hiện chỉ bốn field chính. Thay schema export doanh nghiệp cần version mới và plan compatibility, không thêm/cắt cột ngầm.

## Gates cho task sau

Characterization API + review/golden/audit preservation -> approved input version -> deterministic serial/stream equivalence -> resource bounds -> crash/retry idempotency -> staging/publication -> shadow/canary. OneMaster code là tham chiếu kỹ thuật, không có quyền override P-119 business contract. Feature mới chưa kiểm định default OFF.


## Engine selection / serving adapter

`MDM_ENGINE_V2_ENABLED=false` giữ default an toàn. Private table
`mdm_resolution_engine_selections` pin engine/config theo run; không thay DTO
hoặc reviewer schema. New opt-in run bật decision/cluster/staging cùng nhau;
run đã có serving được grandfather, retry và API manual review dùng profile
đã lưu. Probability không thay rule score; Group/subgroup không đổi.
Group detail tối đa 200 records/2000 pairs, vượt trả HTTP 413 rõ ràng; pair
queue giữ pagination 1..100. Không truncate evidence hoặc sửa UI che lỗi.
Chi tiết rollback, projection và smoke: [ENGINE_ADAPTER](ENGINE_ADAPTER.md).

## Independent worker ownership

FastAPI chỉ enqueue/query/request cancel; execution chạy bằng process worker riêng.
DB claim/attempt/lease/heartbeat, recovery và publication fence được mô tả tại
[WORKERS](WORKERS.md). Existing frontend start/task DTO giữ nguyên.

## Partition artifact / upload contract

Versioned Parquet metadata, closed-file validation, attempt-isolated immutable
partitions, bounded producer admission, OS leases and separate verified remote
upload receipts: [PARTITION_ARTIFACTS](PARTITION_ARTIFACTS.md). This is an additive
storage API; existing stage coordinator, generation publisher and frontend/API
contracts are unchanged. Local computation completion is not durable upload.

## Controlled streaming boundaries

Opt-in native stage adoption, global census/candidate/guarded-union/publication
barriers, independent default-OFF flags, bounded leased reference queues and
explicit serial eligibility fallback: [STREAMING](STREAMING.md). Thread completion
order must never choose business results; local-ready is not durable or serving.

## Safe retry/resume

Versioned dependency-pinned compute checkpoints, atomic owned commits, verified
durable partition reuse, idempotent private publication and conservative leased
GC: [CHECKPOINTS](CHECKPOINTS.md). `PIPELINE_RESUME_ENABLED` defaults OFF. Only
committed valid dependencies may resume; cancellation/retry must preserve the
active generation and reviewer decisions. Migration head is `0025_pipeline_checkpoints`.

## Streaming export

Closed temporary CSV/optional Parquet, exact source coverage, checksum/readback,
completion-before-publication, resource budgets and idempotent export execution:
[EXPORTS](EXPORTS.md). CSV field order/BOM, preview choices/overrides, historical
downloads and R2 redirects are preserved. Direct export can supply optional
`Idempotency-Key` header; the existing request body is unchanged. Local artifacts
use `local:` references without a DB migration.
