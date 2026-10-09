# CP01 — Hợp đồng phiên, trạng thái, KPI và quyền

Ngày: 06/10/2026. Phạm vi: nền tảng cho frontend theo phiên, bám luồng đang chạy trong repo.

## 1. Kiến trúc giữ nguyên

- Next.js → FastAPI `/api/v1/mdm/runs` → service resolution → SQLAlchemy và worker hiện có.
- Một Analysis Session chính là một `ResolutionRun`. Không thêm bảng session, thay ID, thay matcher hoặc viết pipeline mới.
- Cookie `masterone_session`, Origin, CSRF và `owned_run` vẫn là các lớp kiểm tra bắt buộc. Capability được kiểm tra thêm, không thay thế ownership.
- `main.py` hiện đăng ký router run/resolution/auth. Các module legacy import/review và large-imports có trong repo nhưng không được tự bật thêm trong CP01.
- Cấu hình backend hiện mặc định PostgreSQL; SQLite được dùng cho kiểm thử cô lập. Không đổi kết nối, dữ liệu hay role tài khoản trên môi trường đang chạy.
- Không cần migration cho CP01: dùng lại `users.role`, run, task, snapshot và audit event có sẵn. Tên phiên tùy chỉnh chưa triển khai; UI dùng tên file hoặc ID rút gọn.

## 2. Auth and permission contract

`POST /api/v1/auth/login` and `GET /api/v1/auth/me` keep `id`, `email`, and `csrf_token`, and return the stored role and its capabilities. The backend is the permission source of truth. The frontend uses `can(user, capability)`; missing capabilities are denied. Product endpoints use explicit capability checks, while `require_role` remains as a legacy helper.

| Capability | Viewer | Steward | Senior Steward | Policy Admin | Auditor |
|---|---|---|---|---|---|
| read | Yes | Yes | Yes | Yes | Yes |
| create_run / start_run | No | Yes | Yes | No | No |
| approve_pair / reject_pair | No | Yes | Yes | No | No |
| edit_golden / explain | No | Yes | Yes | No | No |
| export_data | No | Yes | Yes | No | No |
| selective_unmerge | No | No | Yes | No | No |
| read_audit | Yes | Yes | Yes | Yes | Yes |
| approve_d_subgroup | No | No | Yes | No | No |
| edit_policy / simulate_policy | No | No | No | Yes | No |

`read_audit`, `approve_d_subgroup`, `edit_policy`, and `simulate_policy` are capability definitions for their corresponding functions; CP01 does not add audit/policy routes or change subgroup policy behavior. `approve_pair` remains subject to existing merge safety checks. Capability checks do not replace session ownership checks. Selective snapshot unmerge is a separate action from rejecting a pending pair.

| API permission checks | Capability |
|---|---|
| GET runs, run, task, overview, items, separate-records, categories, golden-record, snapshot history/comparison | read + owner |
| POST runs | create_run + CSRF/Origin |
| POST run/start | start_run + CSRF/Origin + owner |
| POST review-items/transition, decision=merge/no_merge | approve_pair/reject_pair + CSRF/Origin + owner |
| POST merge-items/.../resolve-conflict | reject_pair + CSRF/Origin + owner |
| PUT proposals/.../golden-record | edit_golden + CSRF/Origin + owner |
| POST item/category explanation | explain + CSRF/Origin + owner |
| POST previews, previews/.../execute, exports | export_data + CSRF/Origin + owner |
| GET source-records.csv, exports/.../download | export_data + owner |
| GET split-preview / POST split | selective_unmerge + owner; POST also checks CSRF/Origin |
| POST export-readiness | read + owner; read-only calculation, no snapshot creation |

Viewer can read records in sessions owned by the account but cannot download detailed CSV or write decisions. There is no Viewer-specific summary export or global run access based only on the Auditor or Policy Admin role.

## 3. Existing account roles

New accounts default to `VIEWER`; registration cannot select a role. Existing role values remain in the database. The offline CLI defaults to dry-run, checks the current role, and changes only one account when explicitly passed `--apply`:

```powershell
uv run --python .venv python -m masterone.auth.manage_role --user-email steward@example.com --expected-role VIEWER --role STEWARD
uv run --python .venv python -m masterone.auth.manage_role --user-email steward@example.com --expected-role VIEWER --role STEWARD --apply
```

Replace the email and expected role with the account's actual values. A role mismatch aborts without writing. Updates use compare-and-set. Do not run automatically at startup, migration, or registration. CP01 did not run this command against a production database.

## 4. Trạng thái và view model

`frontend/src/lib/sessionView.ts` chuyển `ResolutionOverview` và metadata run sang `SessionViewModel`. Không ghi state suy diễn này ngược vào database.

| Trạng thái run | Hiển thị | Hành động theo quyền |
|---|---|---|
| UPLOADED | Đã nạp, chưa xử lý | start |
| QUEUED | Đang chờ xử lý | Xem trạng thái |
| RUNNING | Đang phân giải; persisting = đang lưu kết quả | Xem trạng thái |
| COMPLETED | Đã xử lý | Review/xem trước nếu có quyền; backend vẫn kiểm tra blocker |
| RETRYABLE_FAILED | Xử lý bị gián đoạn | retry |
| FAILED | Xử lý thất bại | retry theo start endpoint hiện có |
| Giá trị chưa nhận biết | Trạng thái chưa được hỗ trợ | Không tự bật mutation hoặc báo thành công |

Ba chiều độc lập:

- `status`: trạng thái pipeline lưu trên server.
- `reviewState`: unavailable/pending/clear; COMPLETED vẫn có thể pending.
- `exportState`: unavailable/not_exported/has_snapshots; has_snapshots không khẳng định snapshot mới nhất đã bao gồm mọi quyết định vừa thay đổi.

`UploadState` mô tả trạng thái cục bộ empty/selected/uploading/validating/uploaded/error; không thêm CREATED/READY/REVIEWED/CLOSED vào database. Drawer dùng đầy đủ state này ở CP06.

Counter hiện cập nhật theo mốc, nên progress chỉ là indeterminate trong QUEUED/RUNNING. `persisting` có thể đạt N/N nhưng chưa COMPLETED. Giá trị 100% chỉ hợp lệ khi trạng thái COMPLETED. CP01 bỏ diễn giải phần trăm sai trong UI hiện tại; telemetry/timeline/polling cải tiến thuộc CP07.

## 5. KPI và đơn vị

| Trường API | Đơn vị | Ý nghĩa |
|---|---|---|
| record_count | record | Bản ghi nguồn đầu vào |
| file_count | file | File đã nạp |
| source_counts | record / nguồn | Không phải điểm chất lượng |
| candidate_count | pair | Cặp được đối chiếu |
| counts.auto | merge_group | Proposal đang Chờ gộp, gồm các trạng thái service đang tính; chưa chắc đủ điều kiện xuất |
| counts.review | review_item | Nhóm review cộng proposal cần xem lại |
| counts.review_cases / manual | review_item | Hai thành phần của counts.review |
| counts.rejected / hard_blocked / insufficient / group_a / group_c / group_d | pair | Các nhóm có thể chồng lấn; không cộng thành số record |
| counts.separate | record | Bản ghi dự kiến giữ riêng theo service hiện tại |
| history_count | snapshot | Số CSV snapshot đã xuất |

Biểu đồ hai hàng đợi ghi tổng “mục công việc”, không ghi số thực thể/khách hàng/bản ghi trùng. Score của matcher không trở thành xác suất hay confidence của Gemini. `actions.prepareExport` trong adapter chỉ là quyền yêu cầu preview; không thay `export-readiness` hoặc quy tắc execute.

## 6. Response bổ sung và hợp đồng dành cho checkpoint sau

`GET /runs/{run_id}/overview` giữ toàn bộ trường cũ, thêm `task: null | ResolutionTaskRead`. Không task nghĩa là run chưa start hoặc run lịch sử không có task; vẫn đọc được. Task hiện một-một với run, không giả định có danh sách attempts.

`ResolutionTaskRead`: `task_id`, `run_id`, `status`, `phase`, `processed_count`, `total_count`, `error_code`, `updated_at`. Được dùng chung cho response overview và `GET /runs/tasks/{task_id}`. Backend khai báo Pydantic response models trong `mdm/schemas.py`; frontend có kiểu tương ứng trong `lib/contracts.ts`.

`getResolutionRuns(limit=25, offset=0)` cho phép caller sử dụng phân trang server hiện có; chưa tự triển khai Hub hoặc bộ lọc server mới.

Các hợp đồng dự kiến sau đây **chưa là endpoint được triển khai ở CP01**:

| Hạng mục | Hợp đồng dự kiến | Phụ thuộc |
|---|---|---|
| Tên phiên | Run có `display_name: string | null`; PATCH `/runs/{id}` nhận display_name, ghi audit | Migration additive + backfill tên fallback + mutation capability được định nghĩa trước khi bật |
| Hub/filter | GET `/runs?limit&offset&q&status&source&created_from&created_to`; items có run_id, display_name, files, created_at, status, source_counts, task summary, review_item_count, merge_group_count | Aggregate/filter phía server và index, không lấy 25 run rồi lọc như toàn bộ dữ liệu |
| Summary toàn cục | GET `/runs/summary`: queued_count, running_count, failed_count, pending_review_item_count, active_runs, updated_at | Owner scope trên toàn bộ tập; route static trước `/{run_id}`; không có export_job_count khi chưa có export jobs |
| Audit read | GET `/runs/{id}/audit?limit&offset&action&pair_id&proposal_id`: total, items(id, run_id, actor_user_id, pair_id, proposal_id, action, details, created_at) | Đã triển khai; owner-scoped + `read_audit` (mọi role trừ Viewer); không dựng event lịch sử |

Không thêm các trường dự kiến này vào response như thể đã được hỗ trợ. Tính toán thật và kiểm thử được thực hiện ở CP03/CP07/CP10 tương ứng.

## 7. Kiểm chứng CP01

- Kiểm thử auth/capabilities qua HTTP, role không xác định, đăng ký không tự nâng quyền, deny write operations for Viewer, Auditor, and Policy Admin; verify ownership and CSRF checks, ownership và CSRF.
- Fixture assigns STEWARD; selective-unmerge cases use SENIOR_STEWARD. Database trong bộ API test là SQLite in-memory; tắt R2/Gemini ở fixture để không dùng cấu hình dịch vụ thật của máy phát triển.
- Kiểm thử xuyên luồng API: upload một CSV → UPLOADED → start (không nhân task khi bấm lặp) → pipeline thật → overview → preview → execute → tải lại cùng bytes.
- Kiểm thử run lịch sử không có task, phục hồi mã WORKER_RESTARTED, phân trang list và decision không hợp lệ.
- Frontend kiểm thử read-only, chặn export dù readiness hợp lệ, adapter trạng thái/đơn vị/tiến độ và toàn bộ hành vi review sẵn có.

Kết quả cuối và lỗi nền được ghi trong `docs/cp01-validation.md`. Không coi test trong bộ CP01 đạt là toàn bộ repo đã qua nghiệm thu CP11.
