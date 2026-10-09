# Chạy thử MasterOne MDM

Đây là demo local MasterOne cho bài toán khử trùng dữ liệu khách hàng PERSON. Mỗi lần nạp tạo một run độc lập; pipeline so khớp bản ghi trong cùng nguồn và giữa CRM, ERP, BILL. Data Steward kiểm tra các item Cần review, chọn giá trị ở Chờ gộp rồi xuất một CSV cuối cùng cho run.

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

Để bật giải thích review bằng Gemini, đặt `GEMINI_API_KEY` trong `.env`; có thể đổi model bằng `GEMINI_MODEL`. Mặc định pipeline không gọi Gemini: phần giải thích nhóm chỉ gửi thống kê tổng hợp khi người dùng yêu cầu. Không có key thì các nhóm vẫn xem được hướng dẫn theo rule.

Có thể bật thêm `SEMANTIC_ADDRESS_ENABLED=true` để Gemini so sánh **chỉ địa chỉ** ở một số cặp cần review có liên hệ khớp và cách viết địa chỉ chưa rõ. Đặt `SEMANTIC_ADDRESS_MAX_PAIRS_PER_RUN` (mặc định 100) để giới hạn số cặp gửi trong mỗi run. Dịch vụ bên ngoài sẽ nhận chuỗi địa chỉ của các cặp đủ điều kiện; tên, điện thoại, email và nhãn `entity_id` không được gửi. Kết quả AI không tự chuyển cặp sang gộp; nếu Gemini lỗi hoặc trả dữ liệu không hợp lệ, hệ thống giữ kết quả rule. Màn hình so sánh sẽ báo khi địa chỉ có tín hiệu hỗ trợ từ Gemini.

## Chạy ứng dụng

Mở **hai terminal PowerShell**, cả hai đều bắt đầu tại thư mục gốc repo. Terminal thứ nhất chạy backend:

```powershell
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001
```

Terminal thứ hai chạy frontend:

```powershell
npm --prefix frontend run dev
```

Mở địa chỉ Next.js in ra khi khởi động (thường là <http://localhost:3000>; nếu cổng này đã dùng, có thể là `localhost:3001`). Trang `/` là landing page; sau khi đăng ký hoặc đăng nhập, không gian làm việc ở `/app`. API docs ở <http://localhost:8001/docs>. Khi chạy local, frontend tự dùng cùng hostname (`localhost` hoặc `127.0.0.1`) để gọi API ở cổng 8001; đặt `NEXT_PUBLIC_API_BASE_URL` trước khi chạy frontend nếu API dùng địa chỉ khác. Backend chỉ chấp nhận các origin loopback theo cổng bất kỳ trong chế độ development; production chỉ chấp nhận các origin liệt kê trong `CORS_ORIGINS`, phân cách bằng dấu phẩy. Dùng cùng hostname cho frontend và backend để cookie hoạt động nhất quán.

Luôn chạy lệnh backend từ **thư mục gốc repo**: `.env`, `backend/alembic.ini` và đường dẫn SQLite tương đối đều được đọc từ đó. SQLite mặc định nằm tại `data/masterone.db`. Khi có migration mới, dừng backend, sao lưu database rồi chạy lại `uv run --python .venv python -m masterone.mdm.migrate` trước khi khởi động. App sẽ báo lỗi nếu schema chưa ở revision mới nhất; nó không tự sửa schema khi khởi động. Trong môi trường production, đặt `AUTH_CSRF_SECRET` là chuỗi bí mật ít nhất 32 ký tự và chạy qua HTTPS.

## Luồng smoke test

1. Đăng ký và đăng nhập.
2. Nạp một hoặc nhiều file như cũ. Bộ thử mới nằm ở `masterone_test/crm.csv`, `erp.csv`, `bill.csv`; mỗi file có `record_id,source,name,phone,email,address` và các cột bổ sung. `entity_id` là nhãn đánh giá offline, không đi vào thuật toán. Có thể nạp cả ba file cùng lúc; các bản ghi trùng trong một file cũng được tìm.
3. Theo dõi run đến `COMPLETED`. Pipeline chuẩn hóa, sinh ứng viên, so sánh theo trạng thái, chấm điểm có điều chỉnh tần suất, áp policy A/C/D, rồi kiểm tra xung đột khi tạo cụm. Tổng quan hiển thị số cụm Chờ gộp và Cần review; mục Dự kiến giữ riêng cho xem các bản ghi chưa nằm trong cụm chờ gộp. Mọi item mở cùng một popup so sánh; giá trị giống nhau chỉ hiện một lần.
4. Trong Cần review, có thể chọn trước giá trị ở trường khác nhau và lưu. Sau đó đưa item sang Chờ gộp hoặc đánh dấu Không gộp; có checkbox để xử lý nhiều item. Nếu item chứa cặp đã xác định là Không gộp, vẫn có thể đưa sang Chờ gộp nhưng item sẽ ở trạng thái chờ tách; chưa có cặp nào được chấp nhận khi chuyển. Có thể đọc hướng dẫn category và yêu cầu Gemini giải thích nếu đã cấu hình.
5. Trong Chờ gộp, lọc item theo nguồn Tự động/Từ review, mở popup và chọn giá trị cho từng trường khác nhau. Item tự động chọn nguồn đầu tiên; item từ review giữ lựa chọn đã lưu. Với item chờ tách, bấm **Tách cụm an toàn**: các liên kết chưa quyết định bị từ chối, các nhóm khớp an toàn được giữ; sau đó chọn giá trị trên từng nhóm còn lại. Nếu chưa thể xuất CSV, giao diện liệt kê item và việc cần xử lý.
6. Bấm **Xem trước CSV kết quả** ở Chờ gộp, kiểm tra số cụm và số dòng dự kiến, rồi xác nhận để tạo CSV. Bản xem trước có hạn 15 phút và phải tạo lại nếu cặp, lựa chọn hoặc chính sách đã thay đổi. Nếu còn item Cần review, xác nhận cảnh báo để xuất; các bản ghi đó được giữ riêng. CSV chứa mỗi bản ghi đầu vào đúng một lần.
7. Trong Lịch sử, so sánh 10 dòng mẫu trước/sau, tải CSV nguồn tái dựng hoặc bản CSV đã xuất. Với bản CSV mới nhất, bấm **Tách bản ghi**, chọn một bản ghi nguồn trong nhóm, xem trước rồi xác nhận. Một bản CSV mới được tạo; nhóm khác giữ nguyên và các liên kết vừa tách được đánh dấu Không gộp.
8. Đăng xuất và đăng nhập tài khoản khác; tài khoản thứ hai không xem được run của tài khoản đầu dù biết ID.

Đánh giá nhãn Linh riêng, không đưa nhãn vào sản phẩm:

```powershell
uv run --python .venv python scripts\evaluate_linh_resolution.py --data-dir masterone_test --output eval\results\linh_policy_v2_selective.json
```

Kết quả offline của policy hiện tại được lưu ở `eval/results/linh_policy_v2_selective.json`. Trên 21.000 bản ghi tổng hợp, blocking tìm thấy 100% cặp trùng cùng nguồn và 99,94% cặp trùng khác nguồn; cluster pair precision 100% và recall 84,59% khi tắt kiểm tra địa chỉ bằng Gemini. Đây là phép đo trên bộ dữ liệu tổng hợp, không bảo đảm hiệu quả với dữ liệu doanh nghiệp thật. Script xuất số liệu riêng cho từng subgroup; không dùng `pairs.csv`.

Mỗi file giới hạn 5 MiB và 10.000 dòng dữ liệu. ID bản ghi không được trùng trong cùng run; nhiều file vẫn được phân giải chung. `pairs.csv` không được nạp qua API. Mỗi lần nạp tạo run độc lập, không so với lần nạp cũ. Dữ liệu nguồn được giữ nguyên; mỗi lần xuất hoặc tách tạo một bản CSV snapshot trong lịch sử. Chạy đúng một backend worker process cho demo.

Policy hiện dùng trọng số rule có điều chỉnh tần suất, chưa phải xác suất đã calibration và chưa dùng Splink. Bộ dữ liệu hiện không có DOB/CCCD đáng tin cho hard constraint định danh, nên không tự tạo tín hiệu này. Gemini giải thích nhóm review hoặc item khi Data Steward yêu cầu. Nếu bật `SEMANTIC_ADDRESS_ENABLED`, Gemini còn hỗ trợ so sánh địa chỉ trong pipeline với giới hạn số cặp; tín hiệu này không được dùng để tự gộp. Các lần nạp vẫn độc lập như phạm vi demo đã chốt.

## Lựa chọn trường và bảo mật

Migration `0011` xóa các bảng của hai luồng prototype cũ cùng bảng lựa chọn trên server; `0012` và `0013` thêm policy evidence, preview, merge lineage và selective unmerge. Nếu còn cần dữ liệu trong những bảng cũ, sao lưu SQLite trước khi nâng cấp. File schema trống cho Supabase là `supabase/masterone_schema.sql`; có thể tạo lại từ model bằng `uv run --python .venv python scripts/generate_supabase_schema.py`.

Lựa chọn chưa xuất được lưu trong `localStorage` theo run. Đổi tab hoặc tải lại trang vẫn giữ lựa chọn; xóa dữ liệu trình duyệt hoặc dùng thiết bị khác sẽ mất lựa chọn chưa xuất. Khi xuất CSV, backend kiểm tra lại nguồn được chọn và tất cả trường bắt buộc. CSV đã xuất được lưu trong database để tải lại từ Lịch sử.

Các cặp Không gộp vẫn được giữ để xem và ngăn gộp sai. Với cặp bị loại tự động, bằng chứng chi tiết được tính lại khi mở item; quyết định đã lưu không thay đổi.

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
