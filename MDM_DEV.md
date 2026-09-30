# Chạy thử MasterOne MDM

Đây là demo local MasterOne cho bài toán khử trùng dữ liệu khách hàng PERSON. Mỗi lần nạp tạo một run độc lập; pipeline so khớp bản ghi trong cùng nguồn và giữa CRM, ERP, BILL. Data Steward kiểm tra các item Cần review, chọn giá trị ở Gộp rồi xuất một CSV cuối cùng cho run.

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

Để bật giải thích review bằng Gemini, đặt `GEMINI_API_KEY` trong `.env`; có thể đổi model bằng `GEMINI_MODEL`. Pipeline không gửi tên, điện thoại, email hoặc địa chỉ sang Gemini; chỉ gửi thống kê nhóm review. Không có key thì các nhóm vẫn xem được hướng dẫn theo rule.

## Chạy ứng dụng

Mở **hai terminal PowerShell**, cả hai đều bắt đầu tại thư mục gốc repo. Terminal thứ nhất chạy backend:

```powershell
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001
```

Terminal thứ hai chạy frontend:

```powershell
npm --prefix frontend run dev
```

Mở <http://localhost:3000>, đăng ký tài khoản rồi đăng nhập. API docs ở <http://localhost:8001/docs>. API mặc định là `http://localhost:8001`; đặt `NEXT_PUBLIC_API_BASE_URL` trước khi chạy frontend nếu API dùng địa chỉ khác. Cấu hình `CORS_ORIGINS` phải liệt kê đúng origin frontend, phân cách bằng dấu phẩy. Dùng cùng hostname `localhost` cho frontend và backend khi chạy local để cookie hoạt động nhất quán.

Luôn chạy lệnh backend từ **thư mục gốc repo**: `.env`, `backend/alembic.ini` và đường dẫn SQLite tương đối đều được đọc từ đó. SQLite mặc định nằm tại `data/masterone.db`. Khi có migration mới, dừng backend, sao lưu database rồi chạy lại `uv run --python .venv python -m masterone.mdm.migrate` trước khi khởi động. App sẽ báo lỗi nếu schema chưa ở revision mới nhất; nó không tự sửa schema khi khởi động. Trong môi trường production, đặt `AUTH_CSRF_SECRET` là chuỗi bí mật ít nhất 32 ký tự và chạy qua HTTPS.

## Luồng smoke test

1. Đăng ký và đăng nhập.
2. Nạp một hoặc nhiều file theo schema của `masterone_test/records.csv`; mỗi hàng có cột `source_system` là CRM, ERP hoặc BILL.
3. Theo dõi run đến `COMPLETED`; mở ba tab Gộp, Không gộp và Cần review. Mọi item mở cùng một popup so sánh; giá trị giống nhau chỉ hiện một lần.
4. Trong Cần review, có thể chọn trước giá trị ở trường khác nhau và lưu. Sau đó chuyển item sang Gộp hoặc Không gộp; có checkbox để xử lý nhiều item. Chỉ các liên kết chưa chắc chắn bị từ chối, các liên kết an toàn vẫn được giữ. Nếu item chứa cặp đã xác định là Không gộp thì không thể đưa cả item sang Gộp; popup hiển thị lý do. Có thể đọc hướng dẫn category và yêu cầu Gemini giải thích nếu đã cấu hình.
5. Trong Gộp, lọc item theo nguồn Tự động/Từ review, mở popup và chọn giá trị cho từng trường khác nhau. Item tự động chọn nguồn đầu tiên; item từ review giữ lựa chọn đã lưu, những trường còn lại cần chọn trước khi xuất.
6. Bấm **Xuất CSV sau gộp** ở Gộp. Nếu còn item Cần review, xác nhận cảnh báo để xuất; các bản ghi đó được giữ riêng. CSV chứa mỗi bản ghi đầu vào đúng một lần, gồm nhóm đã gộp và bản ghi đơn. Mở Lịch sử để so sánh 10 dòng mẫu trước/sau, tải CSV nguồn tái dựng và tải lại từng bản CSV đã xuất.
7. Đăng xuất và đăng nhập tài khoản khác; tài khoản thứ hai không xem được run của tài khoản đầu dù biết ID.

Đánh giá nhãn Linh riêng, không đưa nhãn vào sản phẩm:

```powershell
uv run --python .venv python scripts\evaluate_linh_resolution.py --data-dir masterone_test --output eval\results\linh-resolution.json
```

Kết quả offline hiện tại được lưu ở `eval/results/linh_pipeline_metrics.json`: blocking recall 99,84% trong cùng nguồn và 99,73% giữa nguồn; pair precision 100%, recall 94,79%, hard-negative false-positive rate 0%; cluster pair precision 100%, recall 90,84%. Đây là kết quả trên benchmark được lấy mẫu, không bảo đảm hiệu quả production. Blocking giới hạn mỗi text block tối đa 60 hồ sơ để giữ tài nguyên ở mức demo; benchmark vẫn giữ blocking recall gần 100%.

Mỗi file giới hạn 5 MiB và 5.000 dòng dữ liệu. ID bản ghi không được trùng trong cùng run; nhiều file vẫn được phân giải chung. `pairs.csv` không được nạp qua API. Dữ liệu nguồn được giữ nguyên; mỗi lần xuất tạo một bản CSV snapshot trong lịch sử, có thể tải lại. Chạy đúng một backend worker process cho demo.

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
