# Kiểm tra GĐ2 — upload và Sessions Hub

## Phạm vi

GĐ2 bổ sung upload draft/chia khối, tiếp tục sau gián đoạn, kiểm tra CSV, thay file an toàn và tạo `ResolutionRun` khi xác nhận. Sessions Hub hỗ trợ tìm kiếm, lọc, phân trang cursor, đổi tên và archive/restore. Không đổi thuật toán phân giải. Không đặt trần dung lượng hoặc số dòng của từng file ở tầng ứng dụng; tối đa 10 file mỗi phiên vẫn giữ. Dung lượng thực tế còn tùy disk/R2, reverse proxy, database và worker.

## Kiểm tra tự động

Chạy từ thư mục gốc repo:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_migrations.py backend/tests/test_mdm/test_upload_sessions.py backend/tests/test_mdm/test_sessions_hub.py backend/tests/test_mdm/test_session_contract.py backend/tests/test_mdm/test_rbac.py -p no:cacheprovider -q
.venv\Scripts\ruff.exe check --no-cache backend/src/masterone/api/mdm_runs.py backend/src/masterone/api/mdm_uploads.py backend/src/masterone/mdm/models.py backend/src/masterone/mdm/resolution_import.py backend/src/masterone/mdm/upload_sessions.py backend/migrations/versions/0011_streamline_resolution.py backend/migrations/versions/0017_r2_storage.py backend/migrations/versions/0018_upload_sessions.py backend/tests/test_mdm/test_upload_sessions.py backend/tests/test_mdm/test_sessions_hub.py
```

Chạy tại `frontend`:

```powershell
npm run lint
npm test -- --run
npm run build
```

Kết quả CP2: 25 backend test trọng tâm pass; Ruff pass; frontend 6 file / 32 test pass; TypeScript lint và production build pass. Toàn backend suite có 277 pass, 10 fail nền: bốn test LLM tham chiếu `Settings.llm_enabled`; một test LLM import model `LLMRequest` không có; ba test scoring kỳ vọng khác kết quả hiện tại; hai test address dùng toán tử `&` trên list. Các lỗi này không nằm trong file upload/Hub hoặc migration CP2.

## Chạy giao diện để kiểm tra thủ công

1. Dùng PostgreSQL local/dev, không trỏ migration vào database production. Khởi động database bằng `docker compose up -d database`.
2. Trong một PowerShell ở repo root, đặt `MDM_DATABASE_URL` trỏ tới database local. Nếu dùng mặc định trong `.env.example`:

   ```powershell
   $env:MDM_DATABASE_URL = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"
   $env:PYTHONPATH = "backend/src"
   .\.venv\Scripts\python.exe -m masterone.mdm.migrate --database-url $env:MDM_DATABASE_URL
   .\.venv\Scripts\python.exe -m uvicorn masterone.main:app --reload --app-dir backend/src --port 8101
   ```

3. Trong PowerShell thứ hai, ở `frontend`, chạy:

   ```powershell
   $env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8101"
   npm run dev
   ```

4. Mở `http://localhost:3000`. Dùng tài khoản local có role `STEWARD` để thử thao tác; đăng nhập `VIEWER` để xác nhận chỉ xem. API vẫn là nơi thực thi quyền.

## Kịch bản thao tác

1. Chọn một CSV hợp lệ và một file lớn hơn 5 MiB. Xác nhận giao diện không từ chối theo dung lượng; thấy tiến độ truyền theo file và tạo draft trước khi có run.
2. Tải draft, đóng khu vực upload rồi mở lại draft. Chọn lại đúng file và xác nhận khối đã có được nhận diện qua checksum, phần còn thiếu tiếp tục được gửi.
3. Bắt đầu bằng một file hợp lệ, rồi chọn file thay thế sai schema. Sau khi validate lỗi, xác nhận tên/trạng thái file gốc vẫn được giữ. Chọn file thay thế hợp lệ, validate thành công và xác nhận file mới mới được promote.
4. Đặt tên draft, hoàn tất kiểm tra rồi bấm tạo phiên. Xác nhận run chỉ xuất hiện sau commit và tự được đưa vào bước start; refresh vẫn giữ tên phiên.
5. Trong Hub, tìm theo tên phiên và tên CSV; lọc theo trạng thái/archive; chuyển trang; rename; archive rồi restore. Phiên `QUEUED/RUNNING` không được archive.
6. Đăng nhập Viewer: danh sách phiên của tài khoản vẫn đọc được; không có nút upload/rename/archive/export. Gọi trực tiếp PATCH/bulk với Viewer phải nhận 403.
7. Chuyển light/dark trong trang upload và Hub; xác nhận màu, tiến độ, lỗi, lựa chọn file và bộ lọc vẫn đọc rõ ở cả hai theme.

Test tự động còn xác nhận file >5 MiB, hơn 10.000 dòng, checksum/retry idempotent, bảo toàn file gốc khi thay thế chưa hợp lệ, commit idempotent, cursor pagination và ownership/RBAC. Chưa chạy browser E2E hoặc so pixel với HTML tham chiếu; phần đó thuộc GĐ7. Chưa benchmark pipeline ở quy mô nhiều triệu dòng và chưa triển khai retention/expiry policy vì thời hạn chưa được chốt.
