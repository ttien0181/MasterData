# Chạy thử MasterOne MDM

Đây là prototype local có tài khoản riêng cho từng Data Steward. Matcher dùng so khớp chính xác theo email, điện thoại, mã số thuế và tên. Gợi ý cần được con người kiểm tra; chưa có merge tự động.

## Yêu cầu

- `uv` để cài và chạy Python 3.11; không cần cài Python toàn hệ thống.
- Node.js 22 trở lên và npm (CI dùng Node.js 22).

## Thiết lập lần đầu

Mở PowerShell tại **thư mục gốc repo**. Tạo môi trường Python, cài backend, tạo `.env` nếu chưa có, rồi khởi tạo SQLite:

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run --python .venv python -m masterone.mdm.migrate
```

Cài frontend từ cùng thư mục gốc:

```powershell
npm --prefix frontend ci
```

`npm ci` dùng `frontend/package-lock.json`; chạy lại sau khi clone mới hoặc khi dependencies thay đổi. Không cần cài lại mỗi lần mở ứng dụng. Giữ `.env` hiện có, không chép đè; chỉnh các biến trong đó nếu cần.

## Chạy ứng dụng

Mở **hai terminal PowerShell**, cả hai đều bắt đầu tại thư mục gốc repo. Terminal thứ nhất chạy backend:

```powershell
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8000
```

Terminal thứ hai chạy frontend:

```powershell
npm --prefix frontend run dev
```

Mở <http://localhost:3000>, đăng ký tài khoản rồi đăng nhập. API docs ở <http://localhost:8000/docs>. API mặc định là `http://localhost:8000`; đặt `NEXT_PUBLIC_API_BASE_URL` trước khi chạy frontend nếu API dùng địa chỉ khác. Cấu hình `CORS_ORIGINS` phải liệt kê đúng origin frontend, phân cách bằng dấu phẩy. Dùng cùng hostname `localhost` cho frontend và backend khi chạy local để cookie hoạt động nhất quán.

Luôn chạy lệnh backend từ **thư mục gốc repo**: `.env`, `backend/alembic.ini` và đường dẫn SQLite tương đối đều được đọc từ đó. SQLite mặc định nằm tại `data/masterone.db`. Khi có migration mới, dừng backend, sao lưu database rồi chạy lại `uv run --python .venv python -m masterone.mdm.migrate` trước khi khởi động. App sẽ báo lỗi nếu schema chưa ở revision mới nhất; nó không tự sửa schema khi khởi động. Trong môi trường production, đặt `AUTH_CSRF_SECRET` là chuỗi bí mật ít nhất 32 ký tự và chạy qua HTTPS.

## Luồng smoke test

1. Đăng ký và đăng nhập; chọn `backend/tests/fixtures/mdm/crm_customers.csv` làm CRM và `backend/tests/fixtures/mdm/erp_customers.csv` làm ERP.
2. Nạp dữ liệu và kiểm tra profile cùng hàng đợi ứng viên. Dữ liệu chỉ dùng mẫu tổng hợp.
3. Mở một ứng viên, đọc các tín hiệu khớp và quyết định chấp nhận.
4. Vào Golden Record, chọn nguồn cho từng trường rồi lưu bản nháp.
5. Tải lại trang. Phiên đăng nhập được phục hồi từ cookie `HttpOnly`; ID import đang hoạt động được lưu trong `localStorage` theo user ID. Trạng thái và bản nháp vẫn nằm trong SQLite.
6. Đăng xuất và đăng nhập tài khoản khác; tài khoản thứ hai không xem được import của tài khoản đầu dù biết ID.

Mỗi file giới hạn 10 MiB và 10.000 dòng dữ liệu. Mỗi import trả tối đa 1.000 ứng viên. Chỉ các trường đã định nghĩa được hiển thị trong phần so sánh. Quyết định và lưu bản nháp có audit event; source records không bị sửa.

## Import cũ và bảo mật

Import tồn tại trước migration vẫn ở trong SQLite nhưng chưa có chủ sở hữu nên không hiện trong ứng dụng. Sau khi xác minh chủ sở hữu, dùng lệnh offline sau; mặc định chỉ xem trước, thêm `--apply` để ghi:

```powershell
uv run --python .venv python -m masterone.mdm.claim_legacy_imports --user-email steward@example.com --import-id UUID_CUA_IMPORT
uv run --python .venv python -m masterone.mdm.claim_legacy_imports --user-email steward@example.com --import-id UUID_CUA_IMPORT --apply
```

Mật khẩu được hash bằng Argon2id. Session token chỉ nằm trong cookie và hash SHA-256 trong database; request thay đổi dữ liệu cần `Origin` hợp lệ và `X-CSRF-Token`. Thử đăng nhập sai bị giới hạn theo email/IP trong 15 phút. Bản này chưa có xác minh email, khôi phục mật khẩu, MFA hay chính sách chống đăng ký tự động; cần bổ sung trước khi mở đăng ký công khai trên Internet.

## Kiểm tra

Chạy các lệnh sau từ thư mục gốc repo:

```powershell
uv run --python .venv python -m pytest backend/tests/ -q
uv run --python .venv python -m ruff check backend/src backend/tests backend/migrations
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Trên macOS/Linux, có thể dùng cùng các lệnh `uv` và `npm`; thay lệnh PowerShell tạo `.env` bằng `cp .env.example .env` nếu chưa có file này.

Không dùng CSV chứa dữ liệu khách hàng thật trong prototype này.

Với Docker, chạy lần lượt từ thư mục gốc: `docker compose build backend`, `docker compose run --rm backend python -m masterone.mdm.migrate`, rồi `docker compose up`.
