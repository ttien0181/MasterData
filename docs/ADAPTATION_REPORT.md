# Báo cáo đối chiếu đặc tả MasterOne với repo hiện tại

Ngày kiểm tra: 2026-10-07  
Phạm vi ban đầu: kiểm tra tĩnh, chỉ đọc; không sửa backend/frontend và không chạy hệ thống. Đối chiếu repo với `D:\Downloads\MasterOne_Design_Spec_NextJS.md`, `D:\Downloads\MasterOne_UI_UX_Session_Centric_Concept_v2 (2).md` và các mockup `masterone_login_light (1).html`, `masterone_upload_replace_light.html`, `masterone_upload_sessions_light.html`, `masterone_ui_light.html`. Đây là nguồn tham khảo thiết kế, không phải chỉ thị thay đổi kiến trúc. Đã rà soát nội dung/markup tĩnh của HTML, chưa render bằng browser nên báo cáo không kết luận pixel-level. Các bảng hiện trạng bên dưới là snapshot trước implementation; mục “Cập nhật CP2” ở cuối ghi trạng thái mới hơn.

## Lựa chọn người dùng đã ghi nhận

Người dùng chọn phương án 3 (bổ sung backend) cho: **7** tiến độ upload có sự kiện thật; **9** upload session/replace; **12** draft session; **13** lưu tên phiên và archive; **14** query/bulk endpoints cho Sessions Hub; **16** đọc/lọc/phân trang audit; **17** so sánh nhiều phiên/series; **18** export nâng cao quy mô lớn. Đây là quyết định sản phẩm để đưa vào spec v3, chưa phải implementation contract chi tiết.

Người dùng xác nhận thêm: tên hiển thị là **Phiên phân giải** và cần backend lưu tên tùy chỉnh; muốn upload/draft/replace trước khi tạo phiên; mục tiêu là **nhiều triệu bản ghi**; Viewer chỉ được đọc và không export; cần xem toàn bộ thao tác audit cho Steward trở lên (ưu tiên làm sau); cần archive/restore và retention; cần SSO/OIDC, quên mật khẩu và trạng thái health trên login. Các yêu cầu này vượt contract/giới hạn hiện tại, sẽ đưa vào spec v3 và không triển khai trong bước audit.

Hướng cho 17–18 đã được chọn. Chi tiết chưa chốt:

- **17 — So sánh phiên:** người dùng chọn bổ sung backend để chọn và so sánh nhiều phiên/series. “Chuỗi” là nhóm phiên theo tiêu chí cần chốt; API hiện chỉ xem source CSV so với CSV export trong **một phiên**, không so hai phiên. Cần xác định metric và cách người dùng nhóm phiên.
- **18 — Xuất dữ liệu:** người dùng chọn bổ sung backend cho export quy mô lớn. CSV cơ bản đã có; cần xác định format, giới hạn file, cơ chế chia file, chạy nền/trạng thái, concurrency và thời hạn tải.

## Cập nhật CP2 (2026-10-07)

Theo yêu cầu tiếp tục của người dùng, GĐ2 đã được triển khai trong kiến trúc hiện tại (`frontend/app`, `frontend/src/components`, `frontend/src/lib`, FastAPI/SQLAlchemy): upload draft server-side, chunk 8 MiB có checksum/resume, validate streaming, replace an toàn (file gốc được giữ tới khi thay thế hợp lệ), commit idempotent thành `ResolutionRun`; Sessions Hub có tìm kiếm, lọc, cursor pagination, rename và bulk archive/restore.

Không có giới hạn dung lượng hoặc số dòng cho từng file tại tầng ứng dụng; giới hạn tối đa 10 file/phiên vẫn giữ. Upload theo khối không giới hạn tổng byte, nhưng năng lực thực tế vẫn tùy disk/R2, proxy, database và worker. Chưa benchmark pipeline cho hàng triệu dòng nên không tuyên bố hệ thống đã đạt tải đó. Retention/expiry chưa triển khai vì thời hạn và chính sách purge chưa được chốt.

Kiểm tra: 25 backend test trọng tâm pass; Vitest 6 file / 32 test, `npm run lint`, Ruff trên file CP2 và `npm run build` pass. Toàn bộ backend suite có 277 pass và 10 fail nền ở test LLM/scoring/regression/selective-unmerge; migration và các test CP2 đều pass. Chưa render mockup trong browser để kiểm tra pixel-level.

Các điểm code chính: `backend/src/masterone/api/mdm_uploads.py`, `backend/src/masterone/mdm/upload_sessions.py`, `backend/src/masterone/mdm/resolution_import.py`, `backend/src/masterone/api/mdm_runs.py`, `backend/migrations/versions/0018_upload_sessions.py`, `frontend/src/components/ResolutionIntake.tsx`, `frontend/src/components/SessionsHub.tsx`.

## Kết luận tại thời điểm báo cáo ban đầu

Có thể giữ phần lớn ngôn ngữ hình ảnh của mockup (light/dark tokens, Mo, shell, Sessions Hub, danh sách review), nhưng không thể bê nguyên luồng nghiệp vụ “Session/Series”. Đối tượng thực tế là `ResolutionRun`: mỗi lần tải CSV lập tức tạo một run ở trạng thái `UPLOADED`, rồi người dùng khởi chạy xử lý; không có draft/upload session độc lập, đổi file trong run, lưu trữ/khôi phục, series hay so sánh nhiều run. Giao diện cần ánh xạ trung thực trạng thái, KPI, quyền và hành động từ API hiện tại; các tính năng thiếu dữ liệu hoặc endpoint phải được ẩn/giản lược hoặc chờ backend, theo quyết định của chủ sản phẩm.

Frontend hiện dùng Next.js App Router 16/React 19, CSS thuần, fetch wrapper và state React. Không cần đổi framework hay cài các thư viện đề xuất chỉ để khớp tài liệu. Trong working tree hiện đã có `AppShell`, `SessionsHub`, `SessionDock`, `SessionStatusBar`, bộ theme light/dark và adapter `sessionView`; báo cáo không đánh giá thay đổi đó như tính năng đã được nghiệm thu. Repo đang có các thay đổi chưa commit trước lần kiểm tra này; cần giữ nguyên.

## Bằng chứng về hệ thống hiện tại

| Hạng mục | Quan sát và bằng chứng |
|---|---|
| Frontend | `frontend/package.json`: Next `^16.3.6`, React `^19.3.0`; scripts `build`, `lint` (`tsc --noEmit`), `test` (Vitest). Chỉ có Next/React là dependency runtime; không thấy Tailwind, Radix, TanStack Query/Table/Virtual, Zustand, Zod, React Hook Form, Recharts hay lucide. Router là App Router (`frontend/app/login/page.tsx`, `app/register/page.tsx`, `app/app/page.tsx`). CSS đặt trong `frontend/app/*.css`; API/state trong `frontend/src/lib` và component React hiện hữu. |
| Backend/API/database | FastAPI + SQLAlchemy. `backend/src/masterone/main.py` gắn auth và resolution run routers; không gắn router `large_imports.py`. `backend/src/masterone/config.py` đặt PostgreSQL làm database URL mặc định, có thể override qua cấu hình. Client gọi qua `frontend/src/lib/api.ts`. |
| Session/Run | `GET /mdm/runs` trả danh sách theo tài khoản, `limit/offset` (mặc định 25, tối đa 100); `POST /mdm/runs` nhận multipart CSV và tạo run `UPLOADED`; `POST /{run_id}/start` đưa run vào hàng đợi. Run gắn với owner, không phải một đối tượng Session độc lập. `backend/src/masterone/api/mdm_runs.py`, `mdm/models.py`. |
| Nhập dữ liệu | `backend/src/masterone/mdm/resolution_import.py`: hiện tối đa 10 file/run, mỗi file tối đa 5 MiB và 10.000 dòng; CSV UTF-8-SIG, header theo schema hiện hỗ trợ hoặc schema legacy; loại `pairs.csv`, kiểm tra ID/source/tên và trùng external ID trong file/run. Không có upload chunk/resume trong luồng này. Mục tiêu nhiều triệu dòng cần thay đổi đáng kể giới hạn và kiến trúc nhập/xử lý, không chỉ tăng giới hạn form. |
| Upload lớn khác | Có `api/large_imports.py`, `LargeFileUpload.tsx`, `lib/largeUpload.ts` dùng URL ký và R2, nhưng không thấy router này được mount trong `main.py`; endpoint này thuộc luồng import/staging cũ, không tạo `ResolutionRun`. Không dùng nó làm bằng chứng cho upload lớn của luồng phân giải hiện tại. |
| Pipeline | `resolution_service.py`: load → `blocking` → tính candidate/pair scoring, semantic address enrichment, clustering/proposals và review grouping → `persisting` → lưu kết quả → `COMPLETED`. Worker là thread local trong tiến trình; khởi động lại đánh dấu task đang chạy thành `RETRYABLE_FAILED/WORKER_RESTARTED`. Lỗi pipeline chung thành `RETRYABLE_FAILED/PIPELINE_FAILED`. Không có API hủy. |
| Tiến độ | Task lưu `phase`, `processed_count`, `total_count`; callback công khai hai mốc thô `blocking (0/N)` và `persisting (N/N)`. Đây không phải tiến độ phần trăm của toàn pipeline. Frontend hiện poll overview mỗi 900ms khi queued/running; không tìm thấy SSE/WebSocket trong luồng run. UI nên biểu diễn bước indeterminate, không nội suy phần trăm. |
| Kết quả/KPI | `ResolutionOverviewRead` và `GET /{run_id}/overview` trả status/phase, số record/file/source, candidate pair, số lượng queue, số export snapshot. `counts.auto` là proposal đang chờ/đã duyệt theo trạng thái; `counts.review` là review case + manual proposal; `separate` là ước tính record giữ riêng; A/C/D là số pair. Không có tổng entity đã hợp nhất, tỷ lệ chính xác, KPI chất lượng chung hay time series trong hợp đồng hiện tại. Xem `mdm/schemas.py`, `api/mdm_resolution.py`. |
| Review | Có bucket `merge`, `no_merge`, `review`; list/detail phân trang, filter theo origin/group/subgroup/signature; chuyển item sang merge/no-merge; xử lý staged conflict; xem giải thích; chỉnh golden fields. Decision và override được ghi `ResolutionAuditEvent`. Một số policy/approval rules phụ thuộc subgroup và revision. `api/mdm_resolution.py`, `mdm/resolution_items.py`, `mdm/resolution_review.py`, `mdm/models.py`. |
| Golden | Proposal hiện có `golden_fields`, trạng thái/origin/revision; endpoint đọc record thành viên và override các trường `name, phone, email, address, dob, government_id`. Override hợp lệ theo một số trạng thái và được audit. Không có mô hình Golden entity đã publish độc lập. |
| Export/history | Có readiness/preview/execute, CSV export/download, source CSV, snapshot list, endpoint xem số dòng và tối đa 10 dòng mẫu nguồn so với CSV export của một run, merge events và selective split. Lịch sử là export snapshot theo một run, không phải lịch sử chỉnh sửa đầy đủ hay so sánh 2–5 run. Export có thể nằm trong DB hoặc R2 tùy cấu hình. |
| Auth/roles | Đang có đúng 5 role: `VIEWER`, `STEWARD`, `SENIOR_STEWARD`, `POLICY_ADMIN`, `AUDITOR` (`auth/security.py`). Viewer/Auditor đọc; Steward có create/start/approve/reject/edit Golden/explain/export; Senior Steward thêm một số năng lực subgroup/unmerge; Policy Admin quản lý/mô phỏng policy nhưng không phải Steward. `/auth/me` trả role + capabilities; frontend `can()` fail-closed. Email/password, cookie session, CSRF và rate limit; không tìm thấy SSO/OIDC hay quên/đặt lại mật khẩu. Có `/health` tối giản trả status/env nhưng chưa thấy hợp đồng health-detail cho login. Người dùng yêu cầu Viewer chỉ đọc, SSO/OIDC, quên mật khẩu và health indicator; cần bổ sung contract tương ứng. |
| Lưu trữ/quy mô | Run/file/record và snapshot có foreign key cascade; **KHÔNG TÌM THẤY** API archive/restore, xóa run hay retention/TTL policy. Danh sách có offset pagination. **KHÔNG TÌM THẤY** cam kết SLA, quy mô dữ liệu điển hình, thời gian chạy điển hình, số phiên dự kiến hay giới hạn vận hành lớn/hàng trăm MB cho ResolutionRun. |
| Series | **KHÔNG TÌM THẤY** domain model/API `Series`, nhóm run theo chuỗi, trend endpoint hoặc compare nhiều run. |

## Từ vựng cần dùng khi chuyển thiết kế

| Từ trong thiết kế | Ánh xạ thực tế | Quy tắc nội dung |
|---|---|---|
| Session / phiên | `ResolutionRun` + tên phiên sẽ bổ sung | Dùng **Phiên phân giải**. ID thật là UUID; tên file hiện không phải tên run đã lưu. Người dùng yêu cầu backend lưu tên tùy chỉnh. |
| Processing task | `ResolutionTask` của một run | `QUEUED`, `RUNNING`, `COMPLETED`, `RETRYABLE_FAILED`/`FAILED`; trạng thái run có `UPLOADED` trước khi start. Giữ nguyên status lạ dạng fallback, không tự đổi thành “failed”. |
| Series | Không có | Không hiển thị nhóm chuỗi/trend như dữ liệu thật trước khi có contract/backend. |
| Entity/Golden Record | Proposal + member records + `golden_fields` | Mỗi proposal/cụm là đơn vị trình bày; không cộng thành số entity cuối cùng nếu chưa có khái niệm đó ở API. |
| Review item | Review case hoặc manual proposal, tùy bucket | API `counts.review` cộng hai đơn vị; hiển thị là item/cụm review, không gọi là số cặp hay số khách hàng. |
| History | Resolution export snapshots trong cùng run | Dùng “Lịch sử CSV đã xuất”; không gọi là lịch sử mọi thao tác. Audit sự kiện là một nguồn riêng, chưa có màn audit list được thấy. |
| Archive/draft | Không có | Không tạo trạng thái lưu trữ hoặc draft giả từ local state. |

### Pipeline, input/output và chuyển trạng thái

| Thứ tự thực | Input → output | Phase/status quan sát được |
|---|---|---|
| 1. Upload/parse (trước worker) | CSV bytes → kiểm tra encoding/header/required fields/source/ID/duplicate → `ResolutionRun(UPLOADED)`, `ResolutionFile`, `ResolutionRecord` có raw/normalized fields, source counts và profile trả về cho UI. | Đây là lúc run được tạo; lỗi validation trả HTTP error, chưa bắt đầu pipeline. |
| 2. Queue/load | Run ID → load records và negative edges đã được quyết định trước đó. | `UPLOADED → QUEUED → RUNNING`; task phase `queued/loading`. Retryable task có thể được queue lại. |
| 3. Blocking/candidate generation | Records + negative edges → candidate blocks/pairs. | Progress callback `blocking, 0/N`; không có số hoàn thành trong bước này. |
| 4. Pair scoring/enrichment | Candidate pair + normalized fields/frequencies → match score, decision/reason, field evidence; semantic address enrichment áp dụng giới hạn cấu hình. | Không phát phase/progress riêng cho từng thuật toán con. |
| 5. Clustering/review grouping | Records + scored pairs → merge proposals, review cases, candidate/insufficient counts. | Không có phase riêng được API trả ra. |
| 6. Persist/complete | Computed pairs/proposals/review cases → lưu DB trong transaction, set run/task complete. | Callback `persisting, N/N` trước khi persist; cuối cùng `COMPLETED`, phase `completed`. `N/N` không đồng nghĩa pipeline đã hoàn tất tại thời điểm callback. |
| Lỗi/khởi động lại | Exception → mark task/run `RETRYABLE_FAILED`; worker restart gắn `WORKER_RESTARTED`; exception tổng quát `PIPELINE_FAILED`. | Không có cancel API. Không tìm thấy step-level retry/resume; retry đưa run về worker qua contract hiện tại. |

Trong frontend, polling overview mỗi 900ms khi `QUEUED/RUNNING` là phương án hiện có. Khi task kết thúc (`COMPLETED`, `FAILED`, `RETRYABLE_FAILED`) hoặc run còn `UPLOADED`, dừng poll; lỗi poll mạng không được diễn giải thành pipeline failure. Chưa cần SSE cho contract hiện tại; cập nhật chi tiết hơn cần event/API backend.

## Đối chiếu màn hình và chức năng trong Design Spec §8

Ưu tiên: P0 = cần chốt để có thể triển khai khớp hệ thống; P1 = sau luồng chính; P2 = ngoài phạm vi nếu chưa có backend. “Effort” là tương đối, chỉ cho frontend trừ khi có ghi backend.

| # | Màn hình/chức năng | Trạng thái | Lý do / điều chỉnh đề xuất | Ưu tiên / effort |
|---:|---|---|---|---|
| 1 | Đăng nhập, lỗi, đang xác thực | ĐIỀU CHỈNH | Giữ bố cục/brand/màu; nối email/password hiện có. Người dùng yêu cầu SSO/OIDC, quên mật khẩu và health indicator. `/health` hiện chỉ trả status/env; SSO và recovery cần backend/provider contract. Lỗi xác thực phải chung. | Cao / M + BE |
| 2 | Empty state | ĐIỀU CHỈNH | Hiển thị khi account không có run; CTA nhập file chỉ với capability create/start. Không dựng run mẫu. | Cao / S |
| 3 | Workspace tổng quan | ĐIỀU CHỈNH | Dùng overview thực: files/records/sources, candidate pairs, auto/review items, separate estimate, export count. Không gán KPI không có contract (entity count/quality score). | Cao / M |
| 4 | Processing stepper | ĐIỀU CHỈNH | Chỉ show bước được backend báo: queued/loading/blocking/persisting/completed; phần tính toán bên trong không có progress callback. Không hiện % tiến trình tự suy. | Cao / S |
| 5 | Lỗi phiên | ĐIỀU CHỈNH | Show `error_code`, retry khi `RETRYABLE_FAILED`; mã hiện có tổng quát `PIPELINE_FAILED`, `WORKER_RESTARTED`. Cancel/resume nội bộ chưa có. | Cao / S |
| 6 | Upload: chọn file | CẦN BACKEND BỔ SUNG | Drawer theo mockup, nhưng giới hạn hiện tại (10 files/run, 5 MiB/file, 10.000 rows/file) không đáp ứng mục tiêu nhiều triệu bản ghi. Cần thiết kế ingest/staging/validation và pipeline quy mô lớn trước khi đặt giới hạn mới; con số đích cụ thể còn cần xác nhận. | Cao / XL + BE |
| 7 | Uploading/progress | ĐIỀU CHỈNH | Không có sự kiện tiến độ cho multipart `fetch` API hiện tại. Ba lựa chọn: (1) bỏ số đo chi tiết; (2) trạng thái “đang gửi” indeterminate; (3) backend upload-session/progress API. Không hiện MB/tốc độ/ETA giả. **Bạn chọn 3.** | Cao / S–L |
| 8 | Upload complete + duplicate warning | ĐIỀU CHỈNH | Server trả số dòng/file sau khi tạo run. Filename hash có unique constraint trong run; duplicate external ID từ chối. Không có endpoint kiểm tra trùng trước submit; chỉ cảnh báo lỗi thật từ API. | Cao / S |
| 9 | “Sẵn sàng / Đổi file” | ĐIỀU CHỈNH | POST upload đã tạo run và lưu records; không có draft/upload workspace hay replace API. Lựa chọn: (1) bỏ màn replace, tạo run mới; (2) bản đơn giản chỉ cho thay file local trước POST; (3) yêu cầu backend upload-session/replace. **Bạn chọn 3.** | Cao / M–L |
| 10 | Đang thay file | CẦN BACKEND BỔ SUNG | Không có replace/cancel upload session trong run. Bạn chọn phương án 3 ở dòng 9, nên đây là trạng thái cần backend upload-session/replace đi kèm. | Cao / L |
| 11 | File lỗi | ĐIỀU CHỈNH | Hiển thị lỗi HTTP/schema thực tế, giữ danh sách file local nếu phù hợp; không giả trạng thái backend “replace failed”. | Cao / S |
| 12 | Draft indicator | ĐIỀU CHỈNH | Không có draft server-side. Lựa chọn: (1) bỏ indicator; (2) chỉ đánh dấu file đang chọn local trước upload; (3) bổ sung draft/upload session backend. **Bạn chọn 3.** | Vừa / S–L |
| 13 | Panel phiên | ĐIỀU CHỈNH | Hiển thị run/phase/actions thực và nguồn/file. Người dùng yêu cầu phương án 3: backend lưu tên tùy chỉnh, archive/restore và retention policy. Vai trò được phép archive/restore và thời hạn retention còn cần chốt. | Vừa / L + BE |
| 14 | Sessions Hub, quick view, bulk bar | ĐIỀU CHỈNH | Hub tương ứng `GET /mdm/runs`, phân trang offset. Quick view có thể dùng overview. Bulk/filter server chưa có: (1) bỏ bulk/search filter; (2) chỉ chọn hàng local trên trang hiện tại; (3) bổ sung query/bulk endpoints. **Bạn chọn 3.** | Cao / M–L |
| 15 | Review ba cột | ĐIỀU CHỈNH | Giữ cấu trúc thị giác; lấy queue/detail/actions API thật (merge, no_merge, explain, conflict resolution, chọn field). Ba cột là tổ chức trình bày, không phải API. Hành động nhiều item chỉ qua endpoint transition thật. | Cao / L |
| 16 | Golden, Quality, Audit | ĐIỀU CHỈNH | Golden editor có hỗ trợ proposal fields; Quality chỉ pair evidence/flags trả về. Người dùng xác nhận muốn đọc toàn bộ audit và đã chọn backend read/filter/pagination; UI audit ưu tiên sau. “Steward trở lên” được hiểu tạm là mọi role trừ Viewer, gồm Steward, Senior Steward, Policy Admin và Auditor; cần xác nhận Auditor có được bao gồm không. | Vừa / M + BE |
| 17 | Comparison 2–5 run, trends | CẦN BACKEND BỔ SUNG | Người dùng chọn phương án 3: bổ sung backend để so sánh nhiều phiên/series và xu hướng. API hiện chỉ trả số dòng và tối đa 10 dòng mẫu source CSV so với CSV export của một run; metric/tiêu chí group series cần chốt. | Vừa / L + BE |
| 18 | Export dialog | CẦN BACKEND BỔ SUNG | Người dùng chọn phương án 3: mở rộng backend cho export quy mô lớn. CSV hiện có; format, giới hạn, chia file, background status, concurrency và expiry cần chốt. | Cao / L + BE |

### Các hành vi khác trong đặc tả

| Hành vi | Trạng thái | Lựa chọn cần chốt nếu muốn giữ |
|---|---|---|
| Click row xem nhanh / mở phiên | ĐIỀU CHỈNH | Có thể dùng API run hiện hữu; không có QuickView endpoint riêng. |
| Tạo phiên chỉ khi bấm “xử lý” sau khi upload xong | ĐIỀU CHỈNH | Mâu thuẫn với API: upload đã tạo run. Các lựa chọn ở dòng 9; chưa chốt. |
| Đổi file, rename, archive/restore | CẦN BACKEND BỔ SUNG | Với từng thao tác: (1) bỏ; (2) read-only/simple local behavior nếu vẫn đúng nghiệp vụ; (3) thêm endpoint/model tương ứng. Không tự chọn. |
| Upload chia khối/tiếp tục, hàng trăm MB | CẦN BACKEND BỔ SUNG | `large_imports` không nằm trong luồng ResolutionRun và router chưa mount. (1) bỏ; (2) giữ multipart dưới giới hạn hiện tại; (3) tích hợp upload staging/backend mới. Không tự chọn. |
| Cancel processing | CẦN BACKEND BỔ SUNG | Không có endpoint hủy. (1) bỏ nút; (2) chỉ cho đóng panel/rời trang, task vẫn chạy; (3) thêm cancel endpoint và worker semantics. Chưa chốt. |
| Export tổng hợp cho Viewer | CẦN BACKEND BỔ SUNG | Viewer hiện không có `export_data`. (1) bỏ; (2) chỉ export báo cáo tổng hợp an toàn; (3) bổ sung capability/policy theo phê duyệt. Chưa chốt. |
| Audit browser | CẦN BACKEND BỔ SUNG | Có event ghi, không tìm thấy list/read API active. (1) bỏ tab; (2) hiển thị tối giản sau khi xác minh API phù hợp; (3) bổ sung audit query endpoint. Chưa chốt. |

## Dữ liệu KPI/biểu đồ nên dùng

- Donut hiện có thể biểu diễn **số item chờ gộp (`counts.auto`) so với item cần review (`counts.review`)**; nhãn phải ghi item/queue, không phải record/customer. Nên cân nhắc gọi chart “Phân bổ hàng đợi”.
- KPI hợp lệ: số record đầu vào, file, source counts, candidate pairs, merge proposals/items, review items, estimated separate records, CSV snapshots. A/C/D và insufficient/hard-blocked là số pair; nên dùng nhãn “cặp”.
- Không tìm thấy số entity đầu ra, duplicate records đã gộp, tỷ lệ match chính xác, chất lượng tổng thể, thời gian xử lý/ETA, trend lịch sử nhiều phiên. Ẩn các chart này hoặc đánh dấu BACKEND NEEDED; không suy diễn từ pair/proposal counts.

## Adapter frontend đề xuất

Tiếp tục theo repo (`frontend/src/lib`) và không đưa mock status vào runtime. Có thể giữ/mở rộng `sessionView.ts` thành adapter thuần:

```ts
type RunStatus = "UPLOADED" | "QUEUED" | "RUNNING" | "COMPLETED" |
  "RETRYABLE_FAILED" | "FAILED" | (string & {});

type RunViewModel = {
  id: string;
  title: string;                 // file names hoặc “Phiên <id rút gọn>”, không phải persisted name
  status: RunStatus;
  phase: string;
  progress: { mode: "idle" | "indeterminate" | "complete" | "error";
              processed: number; total: number };
  metrics: { records: number; files: number; candidates: number;
             mergeItems: number; reviewItems: number; separateEstimate: number;
             exports: number; sourceCounts: Record<string, number> };
  actions: { start: boolean; retry: boolean; review: boolean; export: boolean };
  errorCode?: string;
};
```

Map từ `ResolutionOverview` + `ResolutionRunHistory` + `AuthUser.capabilities`. `actions` chỉ để quyết định UI; API/server là nơi chốt quyền. Khi RUNNING, hiện phase/processed/total nếu đúng ngữ nghĩa response nhưng không quy ra phần trăm vì progress là mốc thô. Unknown status vẫn được giữ để render fallback, không normalize sang FAILED. Không thêm `seriesId`, `archived`, `qualityScore`, `entityCount`, upload MB progress hoặc ETA cho đến khi backend có contract.

## Câu hỏi còn mở (xếp theo ảnh hưởng)

1. **Quy mô lớn:** “nhiều triệu” là khoảng bao nhiêu dòng/file/run ở đợt đầu, tối đa bao nhiêu file/GB, bao nhiêu phiên xử lý đồng thời và thời gian xử lý mục tiêu? Các con số này quyết định kiến trúc upload/staging, database, worker, retry và retention.
2. **So sánh phiên (mục 17):** đã chọn bổ sung multi-run compare/series. “Series” nên được tạo thủ công bởi người dùng hay tự nhóm theo nguồn/schema/tên? Giai đoạn đầu chỉ so KPI đang có (record count, candidate pairs, merge/review items, separate estimate) hay cần metric khác? Không có metric chất lượng/độ chính xác trong API hiện tại.
3. **Export (mục 18):** đã chọn export nâng cao. Chốt format (CSV hay thêm XLSX/Parquet), giới hạn dòng/bytes mỗi file, có chia nhiều file không, số export đồng thời và thời hạn URL tải.
4. **Retention/archive:** cần giữ phiên bao lâu và ai được archive/restore (Steward hay chỉ Senior Steward/Admin)? Archive có phải chỉ ẩn khỏi Hub hay đưa dữ liệu sang cold storage?
5. **Audit roles:** đã rõ Viewer không xem; “Steward trở lên” tạm map gồm Steward, Senior Steward, Policy Admin và Auditor. Xác nhận Auditor có được xem toàn bộ audit không? Sự kiện nào cần ghi ngoài những quyết định/Golden override/export/merge-split đã được lưu?
6. **SSO và khôi phục mật khẩu:** dùng nhà cung cấp OIDC nào? Quên mật khẩu sẽ gửi link email hay theo quy trình quản trị/SSO? Ai được tự đăng ký?
7. **Hủy xử lý:** có cần nút hủy run đang chạy không, hay chỉ cho đóng/rời màn hình trong khi worker tiếp tục?

## Đề xuất cập nhật đặc tả và các checkpoint sau duyệt

Giữ nguyên cấu trúc/mục visual, tokens, Mo, typography, component styles và HTML refs trong spec; sửa phần data model, status, permission, API và flow cho khớp hợp đồng thực. Gỡ các dòng “mocks trước API thật” khỏi runtime implementation plan. Giữ Next App Router/TypeScript/CSS hiện tại; có thể cân nhắc thư viện theo feature sau khi chứng minh cần thiết. Light/dark hiện đã có CSS variables ở `frontend/app/themes.css`; kiểm tra tất cả component dùng semantic variables. Spec là nguồn chuẩn hình ảnh: token light `accent #0891b2` và Be Vietnam Pro; app hiện khai báo `--primary: #087e99` và Source Sans 3. Trong lần làm UI đã được duyệt, align token/font với spec và kiểm tra contrast/a11y.

Người dùng đã duyệt báo cáo, cho phép code GĐ 0–1 rồi tiếp tục duyệt GĐ2. `docs/MasterOne_Design_Spec_NextJS_v3.md` được dùng làm spec đã hiệu chỉnh. GĐ0–2 đã triển khai; các giai đoạn sau vẫn chưa được làm trong checkpoint này.

1. **GĐ0 — nền/theme:** theo convention Next/CSS hiện tại; align tokens/typography với spec, xác minh light/dark, responsive, focus/reduced-motion/a11y.
2. **GĐ1 — auth/shell:** làm shell và login theo reference HTML; nối auth/me/capabilities hiện có. Kiểm tra hai theme và role Viewer/Steward cùng các role đang tồn tại.
3. **CP2 — Upload sessions + Sessions Hub:** đã bổ sung backend draft/upload-session, progress/retry/replace an toàn, tên phiên, archive/restore, pagination/filter/bulk và server-side authorization. Không đặt giới hạn byte hoặc số dòng ở tầng ứng dụng; giới hạn 10 file/phiên giữ nguyên. Chốt load target trước khi benchmark; retention/expiry vẫn chờ policy.
4. **CP3 — overview/processing/error:** map KPI đúng đơn vị; poll trạng thái thật; không fake percentage; retryable failure/reload/deep-link. Kiểm tra queued/running/completed/worker restart/error và polling dừng đúng lúc.
5. **CP4 — Review/Golden/explanation/audit:** list/detail, capability-gated decisions, revision/conflict, override fields và audit read API cho role được chốt. Kiểm thử Viewer không mutate; Steward happy/error paths; audit đầy đủ và không mất thay đổi khi refresh.
6. **CP5 — Export/history/compare:** readiness/preview/execute/download, snapshots, compare source-vs-export hiện có. Kiểm tra blocker, acknowledge, download CSV và ownership; triển khai multi-run compare/trend hoặc export quy mô lớn theo các quyết định ở câu hỏi 2–3.
7. **CP6 — hoàn thiện visual/regression:** pixel review so HTML reference, light/dark, viewport mobile/desktop, keyboard/focus/reduced motion, build/typecheck/Vitest và backend tests phù hợp. Dừng để duyệt checkpoint trước khi chuyển bước.

Các mức effort trong bảng chỉ là ước lượng sơ bộ từ phạm vi hiện thấy; feature BACKEND NEEDED cần estimate lại sau khi chốt câu hỏi liên quan. GĐ0–2 đã được triển khai theo các checkpoint người dùng duyệt; các giai đoạn tiếp theo chưa được code trong lượt này.
