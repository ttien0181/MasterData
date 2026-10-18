# MasterOne

MasterOne là ứng dụng web **khử trùng dữ liệu khách hàng (Entity Resolution) có người duyệt (HITL)**. Data Steward nạp nhiều file CSV từ các hệ thống nguồn (CRM, ERP, BILL, CSV chăm sóc khách hàng); hệ thống tìm các hồ sơ có thể thuộc cùng một khách hàng, **đề xuất** nhưng không tự gộp, để người duyệt quyết định từng cặp, rà soát từng nhóm, chọn giá trị đúng cho **hồ sơ đại diện (Golden Record)** và xuất CSV kết quả.

Đề bài gốc: *Entity Resolution cho khách hàng và nhà cung cấp* (DATA-17). Phạm vi sản phẩm hiện tại: **chỉ gộp thực thể loại PERSON**; ORGANIZATION được nhận dạng nhưng bị chặn gộp theo chính sách.

| Thành phần | Công nghệ |
|---|---|
| Frontend | Next.js 16, React 19, TanStack Query (`frontend/`) |
| Backend | FastAPI, SQLAlchemy 2, Alembic (`backend/src/masterone/`) |
| Worker | Tiến trình riêng `masterone.mdm.resolution_worker`, nhận việc qua hàng đợi trong database |
| Lưu trữ | PostgreSQL (local hoặc Neon); Cloudflare R2 (tùy chọn) cho file upload và artifact; SQLite chỉ dùng cho test cô lập |
| AI qua API | Embedding OpenRouter `openai/text-embedding-3-small` (tùy chọn theo phiên); Gemini cho giải thích và tư vấn cặp, OpenRouter chat là dự phòng; mọi lời gọi đi qua cổng chính sách `llm/policy.py` |

Luật và truy hồi ứng viên chạy bằng thuật toán cục bộ, không gọi LLM. AI **không** có quyền gộp: chỉ cung cấp bằng chứng và lời giải thích để người duyệt quyết định.

## Luồng sử dụng

Mỗi lần tải lên tạo một **phiên (run)** độc lập, không đối chiếu với phiên cũ. Mỗi phiên có bốn bước, tương ứng bốn tab trong `/app/sessions/{runId}`:

| Bước | Tab | Việc của người dùng | Hệ thống làm gì |
|---|---|---|---|
| 0 | `/app/uploads/new` | Chọn tối đa 10 CSV, xem kiểm tra, bấm tạo phiên (có thể bật tùy chọn embedding) | Tải lên theo phần (resumable), kiểm tra header/ID/nguồn, chuẩn hóa, lưu bản ghi nguồn |
| 1 | **Hồ sơ khách hàng** (`data`) | Xem tổng quan: hồ sơ theo nguồn, mức độ đầy đủ thông tin, các điểm cần kiểm tra; duyệt danh sách hồ sơ | Chuẩn bị dữ liệu (`PREPARE`) rồi tự chạy phân loại (`DECIDE`) |
| 2 | **Các cặp hồ sơ khách hàng** (`decisions`) | Duyệt cặp theo nhóm: **Đồng ý gộp** / **Không gộp** / Từ chối ghép cặp (Deny) / Hoãn; duyệt hàng loạt có xem trước | Sinh ứng viên, chấm bằng rule (kèm bằng chứng embedding nếu bật), phân loại cặp thành nhóm A/B/C/BLOCKED; Gemini chỉ giải thích khi người dùng yêu cầu |
| 3 | **Các nhóm hồ sơ khách hàng** (`clusters`) | Rà soát nhóm đã nối từ các cặp được chấp nhận, xác nhận nhóm 2–20 thành viên (nhóm 21–100 qua luồng riêng) | Tạo nhóm (`CLUSTER`) kiểm tra ràng buộc cứng và liên kết gián tiếp |
| 4 | **Hồ sơ đại diện** (`golden`) | Chọn giá trị đúng cho từng trường, xác nhận, chọn hồ sơ cần xuất | Đề xuất Golden bằng quy tắc survivorship, xuất CSV |

Tab *Tiện ích* (`utilities`) hiển thị lịch sử xử lý, các lần xuất và quy định áp dụng. Chi tiết thao tác: [Hướng dẫn sử dụng](docs/USER_GUIDE.md).

### Chế độ demo

Màn hình đăng nhập có nút **Demo sản phẩm** (bật bằng `DEMO_ENABLED=true`). Hệ thống tạo tài khoản tạm vai trò Steward, nạp sẵn bộ dữ liệu giả lập `customer-demo-v1` (4 file × 500 hồ sơ, tải được ở trang Tổng quan) và hướng dẫn từng bước kèm trợ lý MasterBot. Phiên demo tự hết hạn (mặc định 2 giờ, nhàn rỗi 1 giờ) và bị dọn bởi tác vụ retention.

## Chạy local

Cần [uv](https://docs.astral.sh/uv/) (quản lý Python 3.11+), Node.js 22+ và PostgreSQL (local hoặc Neon). Mở PowerShell ở **thư mục gốc repo**:

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
npm --prefix frontend ci
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Sửa `.env` (xem bảng dưới), rồi migrate và chạy ba tiến trình:

```powershell
uv run --python .venv python -m masterone.mdm.migrate          # đưa schema lên revision mới nhất

# Terminal 1: API
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001
# Terminal 2: worker (bắt buộc, API không tự xử lý pipeline)
uv run --python .venv python -m masterone.mdm.resolution_worker
# Terminal 3: giao diện
npm --prefix frontend run dev
```

Mở <http://localhost:3000>, đăng ký, nâng vai trò lên Steward (xem [Vai trò](#vai-trò-và-quyền)), đăng nhập và vào `/app`. Swagger của API ở <http://localhost:8001/docs>. Luôn chạy backend từ thư mục gốc để tìm đúng `.env` và migration; app từ chối khởi động nếu schema chưa ở revision mới nhất (hiện là `0039_optional_embedding`). Dùng `localhost` thống nhất cho cả frontend và API vì cookie phiên bị từ chối khi lẫn `127.0.0.1`.

### Docker Compose

`docker-compose.yml` (project `p119-session-qa`) chạy `backend`, `worker`, `frontend` và tùy chọn `database` (profile `local-db`). Cần file `.env.qa` (mẫu: `.env.qa.example`); backend và worker dùng chung image, mount `./data`. Triển khai thật dùng `docker-compose.production.yml` + Caddy: xem [Vận hành](docs/OPERATIONS.md).

```powershell
docker compose run --rm backend python -m masterone.mdm.migrate
docker compose up -d --build
```

### Biến môi trường chính

Không commit `.env`. Danh sách đầy đủ và các cờ tính năng: [docs/OPERATIONS.md](docs/OPERATIONS.md#cấu hình).

| Biến | Công dụng |
|---|---|
| `MDM_DATABASE_PROFILE`, `MDM_DATABASE_URL_LOCAL`, `MDM_DATABASE_URL` | Chọn `local` hoặc `neon` (mặc định trong code là `neon`) và chuỗi kết nối tương ứng |
| `AUTH_CSRF_SECRET` | Bí mật ≥ 32 ký tự, bắt buộc khi `APP_ENV=production` |
| `CORS_ORIGINS` | Origin frontend được phép (development tự chấp nhận loopback) |
| `SESSION_WORKFLOW_ENABLED` | **Phải là `true`** để dùng luồng phiên bốn bước; mặc định `false`, và `.env.example` chưa đặt biến này |
| `MATCHING_PROFILE_REGISTRY_ENABLED` | `true` để mỗi phiên được gắn matching profile (cần cho xử lý và demo) |
| `WORKFLOW_AUTO_PROCESS` | `true` để tự chạy `DECIDE` sau khi chuẩn bị dữ liệu xong |
| `DEMO_ENABLED` | Bật nút Demo sản phẩm |
| `AI_API_ONLY` | `true` chặn E5/Splink cục bộ; Compose luôn đặt `true` |
| `EMBEDDING_OPENROUTER_API_KEY`, `ADVANCED_MATCHING_AGENTS_ENABLED` | Bật tùy chọn embedding khi tạo phiên |
| `GEMINI_API_KEY`, `LLM_ENABLED`, `LLM_EXPLANATION_ENABLED`, `LLM_APPROVED_PROVIDERS`, `LLM_APPROVED_MODELS` | Cho phép giải thích và tư vấn cặp; mặc định **tắt, từ chối mọi lời gọi** cho đến khi được duyệt tường minh |
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Lưu file upload và artifact trên Cloudflare R2; bỏ trống thì dùng `UPLOAD_STAGING_DIR`/`WORKFLOW_ARTIFACT_DIR` trên đĩa |
| `NEXT_PUBLIC_API_BASE_URL` | Địa chỉ API cho frontend khi khác `localhost:8001` |

## Dữ liệu đầu vào

CSV UTF-8, tối đa **10 file mỗi phiên**; ứng dụng không đặt giới hạn dòng hay dung lượng (file được tải theo phần 8 MiB và xử lý streaming). Header được ánh xạ qua hợp đồng `p119_input_v1` (`backend/src/masterone/mdm/input_contract_v1.json`):

| Trường | Bắt buộc | Tên cột chấp nhận (không phân biệt hoa thường) |
|---|---|---|
| `source_record_id` | Có | `source_record_id`, `record_id`, `id`, `contact_id`, `debtor_code`, `account_number`, `MA_KH` |
| `source_system` | Có, giá trị `CRM`/`ERP`/`BILL`/`CSV` | `source_system`, `source` |
| `name` | Có | `full_name`, `name`, `contact_name`, `legal_name`, `HO_TEN`, … |
| `phone`, `email`, `dob`, `address`, `government_id` | Không | `phone`/`phone_number`/`mobile`/`DIEN_THOAI`, `email`, `dob`/`date_of_birth`/`NGAY_SINH`, `address`/`raw_address`/`DIA_CHI`, `government_id`/`cccd`/`cmnd` |
| Metadata xác minh (`government_id_verified`, `phone_verified`, …) | Không | Tên cột đúng như hợp đồng; thiếu metadata nghĩa là **chưa xác minh**, không suy diễn |
| `entity_id` / `ground_truth` | Không | Chỉ để đánh giá offline; không đi vào thuật toán |

Cột ngoài hợp đồng được giữ ở `extra_fields`. ID bản ghi phải duy nhất trong một phiên. Bộ mẫu để thử: `masterone_test/crm.csv`, `erp.csv`, `bill.csv` (7.000 dòng mỗi file, cột kiểu `record_id,entity_id,source,…`) và dữ liệu demo tải từ `/api/v1/mdm/demo/files/masterone-du-lieu-mau.zip`. **Không nạp dữ liệu khách hàng thật vào bản demo.**

## Vai trò và quyền

Đăng ký luôn tạo tài khoản `VIEWER` (chỉ xem). Muốn thao tác, quản trị viên đổi vai trò offline (mặc định dry-run, thêm `--apply` để ghi):

```powershell
uv run --python .venv python -m masterone.auth.manage_role --user-email you@example.com --expected-role VIEWER --role STEWARD
```

| Vai trò | Quyền chính |
|---|---|
| `VIEWER` | `read` |
| `AUDITOR` | `read`, `read_audit` |
| `STEWARD` | tạo/chạy phiên, duyệt (`approve_pair`, `reject_pair`), sửa Golden, giải thích AI, xuất dữ liệu, quản lý phiên, profile matching và ràng buộc cấu trúc |
| `SENIOR_STEWARD` | Như Steward, thêm duyệt nhóm con D và tách bản ghi đã gộp (`selective_unmerge`) |
| `POLICY_ADMIN` | `read`, `read_audit`, sửa/mô phỏng policy; **không** có quyền steward |

Quyền luôn được kiểm tra ở backend; frontend chỉ ẩn/hiện nút. Phiên demo bị giới hạn ở tập quyền thao tác của Steward.

## Kiểm thử

```powershell
uv run --python .venv python -m pytest backend/tests/<file> -q   # nên chạy từng file; gom cả bộ rất chậm
uv run --python .venv python -m ruff check backend/src backend/tests backend/migrations
npm --prefix frontend test       # vitest
npm --prefix frontend run lint   # tsc --noEmit
npm --prefix frontend run build
npm --prefix frontend run test:e2e   # Playwright, cần stack đang chạy
```

CI (`.github/workflows/ci.yml`) chạy ruff, pytest `backend/tests/` và các lệnh frontend. Hướng dẫn chi tiết, bản đồ test và script: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Giới hạn hiện tại

- Điểm matching là trọng số rule có điều chỉnh tần suất, **không phải xác suất đã hiệu chỉnh**. Gemini/embedding không tự quyết định gộp.
- Chỉ PERSON được gộp; ORGANIZATION bị chặn theo chính sách. Nhóm gộp tối đa 20 thành viên (luồng riêng đến 100); nhóm lớn hơn bị chặn, không tự chia nhỏ.
- Chất lượng đo trên dữ liệu **synthetic** của mentor (TC1/TC3), chưa phải nhãn người độc lập. Tự động gộp (`PAIR_AUTOMATION_ENABLED`) tắt mặc định; trên TC3 pipeline chưa tạo đề xuất gộp mức cao, chất lượng cuối phụ thuộc người duyệt. Xem [Đánh giá và bằng chứng](docs/EVALUATION.md).
- Một worker và các tác vụ nặng được tuần tự hóa (`HEAVY_JOBS_SERIALIZED`); chưa chứng nhận tải đồng thời hoặc dữ liệu hàng trăm nghìn dòng.
- Trước khi mở Internet công khai cần bổ sung xác minh email, khôi phục mật khẩu, MFA và chống đăng ký tự động.
- Giao diện luôn tạo phiên `workflow_version=2` (bốn bước). Phiên `workflow_version=1` cũ chỉ còn xem được ở `/app/legacy` (chỉ đọc, trạng thái `LEGACY_READ_ONLY`).

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md) | Yêu cầu sản phẩm (BA/PM): bài toán, persona, yêu cầu theo trạng thái triển khai, quy tắc nghiệp vụ, KPI, backlog |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Hướng dẫn thao tác cho Data Steward |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kiến trúc, module, máy trạng thái, mô hình dữ liệu |
| [docs/MATCHING_ENGINE.md](docs/MATCHING_ENGINE.md) | Pipeline matching, nhóm A/B/C/BLOCKED, ràng buộc cứng, clustering, Golden |
| [docs/API.md](docs/API.md) | Tham chiếu endpoint |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Cấu hình, triển khai, runbook, dọn dẹp |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Cấu trúc repo, kiểm thử, script, log AI |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Số đo chất lượng/hiệu năng và giới hạn |
| [docs/reference/ENTITY_RESOLUTION_SPEC_v3.1.md](docs/reference/ENTITY_RESOLUTION_SPEC_v3.1.md) | Đặc tả thiết kế tham chiếu v3.1 (một phần chưa triển khai) |

Đóng góp theo [CONTRIBUTING.md](CONTRIBUTING.md), báo lỗi bảo mật theo [SECURITY.md](SECURITY.md); dự án dùng giấy phép [MIT](LICENSE).
