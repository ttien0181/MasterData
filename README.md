# MasterOne

MasterOne là MVP khử trùng dữ liệu khách hàng. Data Steward nạp nhiều CSV trong một lần chạy, xem các cụm hệ thống đề xuất gộp hoặc cần người duyệt, chọn giá trị chuẩn, xem trước và xuất một CSV kết quả. Hệ thống tìm trùng **trong cùng nguồn và giữa các nguồn**; mỗi lần nạp là một run độc lập.

- Frontend: Next.js trong `frontend/`
- Backend: FastAPI trong `backend/src/masterone/`
- Lưu trữ local: SQLite qua SQLAlchemy, quản lý schema bằng Alembic
- Gemini: giải thích hồ sơ theo yêu cầu; đối chiếu địa chỉ chỉ khi bật cấu hình

Xem [sơ đồ components và luồng dữ liệu](docs/architecture_diagram.md), [bằng chứng 5 ca manual](eval/results/report.md) và [hướng dẫn phát triển chi tiết](MDM_DEV.md).

## Chạy local

Cần [uv](https://docs.astral.sh/uv/) (quản lý Python 3.11+, không cần cài Python toàn hệ thống), Node.js 22+ và npm. Mở PowerShell ở **thư mục gốc repo**:

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
npm --prefix frontend ci
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run --python .venv python -m masterone.mdm.migrate
```

Mở hai terminal tại thư mục gốc:

```powershell
# Terminal 1: API
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001
```

```powershell
# Terminal 2: giao diện
npm --prefix frontend run dev
```

Mở <http://localhost:3000> (hoặc cổng Next.js in ra), đăng ký/đăng nhập và vào `/app`. API ở <http://localhost:8001>; tài liệu endpoint ở <http://localhost:8001/docs>. Chạy backend từ thư mục gốc để tìm đúng `.env`, migration và database. Khi có migration mới, dừng backend, sao lưu database, rồi chạy lại lệnh migrate trước khi khởi động.

### Biến môi trường

Tạo `.env` từ `.env.example`; **không commit key hoặc file `.env`**.

| Biến | Công dụng |
|---|---|
| `MDM_DATABASE_URL` | Database backend; mặc định `sqlite:///./data/masterone.db`. File SQL trong `supabase/` không tự kết nối Supabase. |
| `CORS_ORIGINS` | Origin frontend được phép trong production; local development chấp nhận origin loopback. |
| `AUTH_CSRF_SECRET` | Bắt buộc là bí mật ít nhất 32 ký tự khi `APP_ENV=production`. |
| `GEMINI_API_KEY` | Chỉ cần khi dùng nút giải thích bằng Gemini hoặc bật đối chiếu địa chỉ. |
| `GEMINI_MODEL` | Model Gemini; mặc định `gemini-3.5-flash-lite`. |
| `SEMANTIC_ADDRESS_ENABLED` | Mặc định `false`; khi bật, Gemini hỗ trợ so sánh địa chỉ ở một số cặp Cần review, không tự quyết định gộp. |
| `SEMANTIC_ADDRESS_MAX_PAIRS_PER_RUN`, `SEMANTIC_ADDRESS_TIMEOUT_SECONDS` | Giới hạn số cặp và thời gian chờ khi dùng đối chiếu địa chỉ. |
| `AI_LOG_API_KEY` | Key ghi log quá trình dùng công cụ AI cho chương trình; tách biệt với `GEMINI_API_KEY` của ứng dụng. |

`OPENAI_API_KEY` còn trong file mẫu cho agent template; luồng MDM không cần key này. Nếu frontend phải gọi API ở địa chỉ khác, đặt `NEXT_PUBLIC_API_BASE_URL` cho Next.js trước khi chạy (ví dụ trong `frontend/.env.local`).

## Dữ liệu mẫu và luồng sử dụng

Bộ CSV của Linh ở `masterone_test/crm.csv`, `erp.csv`, `bill.csv`. Mỗi file có 7.000 dòng; có thể chọn cả ba cùng lúc. Các cột bắt buộc là `record_id,source,name,phone,email,address`; `source` nhận `CRM`, `ERP` hoặc `BILL`. `record_id` phải duy nhất trong một run. Cột `entity_id`, nếu có, chỉ dùng để đánh giá offline; `pairs.csv` không phải đầu vào ứng dụng. Giới hạn hiện tại: tối đa 10 file/run, 5 MiB và 10.000 dòng/file.

1. **Nạp dữ liệu:** chọn một hoặc nhiều CSV; backend kiểm tra, lưu bản ghi nguồn và chuẩn hóa trường.
2. **Phân giải:** worker sinh cặp ứng viên, so sánh bằng rule/policy và tạo cụm. Tổng quan cho biết số item **Chờ gộp** và **Cần review**.
3. **Duyệt:** mở item Cần review để so sánh, có thể đưa sang Chờ gộp hoặc đánh dấu Không gộp. Nút Gemini chỉ tạo lời giải thích hỗ trợ.
4. **Chốt giá trị:** trong Chờ gộp, mở item và chọn nguồn cho các trường khác nhau. Item từ review có thể mang theo lựa chọn đã lưu trong trình duyệt.
5. **Xuất:** xem trước số dòng, xử lý các item bị chặn, xác nhận cảnh báo nếu còn review chưa duyệt, rồi tải CSV. Bản ghi chưa duyệt được giữ riêng. Lịch sử lưu snapshot để tải lại và xem trước/sau.

## Ví dụ truy vấn API

`GET /health` không cần đăng nhập:

```powershell
Invoke-RestMethod http://localhost:8001/health
```

Các truy vấn sau cần cookie phiên của tài khoản đã đăng nhập; dùng browser/DevTools hoặc <http://localhost:8001/docs> với phiên phù hợp. Thay `{run_id}` bằng ID run của bạn.

| Yêu cầu | Mục đích |
|---|---|
| `GET /api/v1/mdm/runs` | Liệt kê các lần nạp của tài khoản. |
| `GET /api/v1/mdm/runs/{run_id}/overview` | Lấy trạng thái và số lượng trên Tổng quan. |
| `GET /api/v1/mdm/runs/{run_id}/categories` | Lấy các nhóm trường hợp Cần review. |
| `GET /api/v1/mdm/runs/{run_id}/items/review?limit=25` | Xem trang đầu của hàng đợi review. |
| `GET /api/v1/mdm/runs/{run_id}/items/merge?limit=25` | Xem trang đầu của Chờ gộp. |
| `GET /api/v1/mdm/runs/{run_id}/exports` | Xem các CSV snapshot trong Lịch sử. |

Các request thay đổi dữ liệu cần cookie phiên, `Origin` hợp lệ và `X-CSRF-Token`; giao diện thực hiện các bước này. Hợp đồng request/response đầy đủ ở Swagger UI.

## Kiểm thử và bằng chứng

```powershell
uv run --python .venv python -m pytest backend/tests/ -q
uv run --python .venv python -m ruff check backend/src backend/tests backend/migrations
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

[Báo cáo manual MVP](eval/results/report.md) liên kết ảnh chụp và CSV kết quả thực tế. Đây là bằng chứng thao tác sản phẩm; test tự động trong `backend/tests/` và `frontend/` kiểm tra hành vi code. Script `scripts/evaluate_linh_resolution.py` dùng để đo thuật toán offline, không thay thế 5 ca manual.

## Phạm vi hiện tại

Các run không so khớp chéo nhau. Điểm matching là trọng số rule có điều chỉnh tần suất, **không phải xác suất đã hiệu chỉnh** và pipeline không dùng Splink. Gemini không tự quyết định gộp. MVP local dùng SQLite và một worker backend; trước khi triển khai Internet công khai cần bổ sung xác minh email, khôi phục mật khẩu, MFA và chính sách chống đăng ký tự động. Không nạp dữ liệu khách hàng thật vào bản demo.

## Cấu trúc repo và log phát triển

- `backend/`: FastAPI, auth, MDM pipeline, migrations và pytest.
- `frontend/`: Next.js và kiểm thử giao diện.
- `masterone_test/`: CSV demo của Linh.
- `docs/architecture_diagram.md`: sơ đồ hệ thống đang chạy.
- `eval/results/`: bằng chứng manual và CSV xuất.
- `scripts/`: đánh giá offline và hook ghi log AI.

Nếu đội cần log việc dùng Codex/Claude/Gemini theo yêu cầu chương trình, mỗi thành viên đặt `AI_LOG_API_KEY` của mình trong `.env`, rồi cài hook một lần:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1
```

Hướng dẫn đầy đủ cho các hệ điều hành và công cụ khác ở [MDM_DEV.md](MDM_DEV.md) và `scripts/`. Đóng góp theo [CONTRIBUTING.md](CONTRIBUTING.md); dự án dùng [MIT](LICENSE).
