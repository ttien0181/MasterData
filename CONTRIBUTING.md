# Đóng góp cho MasterOne

Cảm ơn bạn đã quan tâm. Hướng dẫn cài đặt, cấu trúc repo và cách chạy kiểm thử: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Kiến trúc: [ARCHITECTURE.md](ARCHITECTURE.md).

## Quy trình

1. Tạo nhánh từ `main`: `git checkout -b feat/ten-tinh-nang`
2. Commit theo [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`, `perf:`
3. Chạy kiểm tra trước khi push (từ thư mục gốc repo):
   ```powershell
   uv run --python .venv python -m ruff check backend/src backend/tests backend/migrations
   uv run --python .venv python -m pytest backend/tests/<file-lien-quan> -q
   npm --prefix frontend test
   npm --prefix frontend run lint
   npm --prefix frontend run build
   ```
   Nên chạy pytest theo file liên quan; bộ đầy đủ thu thập rất chậm (xem [DEVELOPMENT](docs/DEVELOPMENT.md#4-kiểm-thử)). CI (`.github/workflows/ci.yml`) chạy ruff, pytest `backend/tests/`, vitest, `tsc --noEmit` và build frontend.
4. Mở PR vào `main`, mô tả thay đổi và cách đã kiểm chứng.

## Quy tắc thay đổi

- Thay đổi hành vi của engine matching phải đi sau cờ cấu hình, được ghim theo run và có test hồi quy; không đổi âm thầm kết quả của phiên cũ.
- Mọi lệnh ghi của luồng phiên đi qua operation có `request_id`, kiểm tra revision và audit.
- Mọi lời gọi LLM đi qua `llm/policy.py`; không thêm đường gọi trực tiếp.
- Migration luôn additive và có test; không sửa revision đã phát hành.
- Cập nhật tài liệu tương ứng trong `docs/` và `README.md` khi đổi hành vi, endpoint, cấu hình hoặc quyền. Giữ tài liệu mô tả **trạng thái hiện tại**; không thêm báo cáo theo ngày hoặc nhật ký phiên làm việc vào `docs/` (kết quả đo ghi vào [docs/EVALUATION.md](docs/EVALUATION.md)).

## Không commit thông tin bí mật

Tuyệt đối không commit API key, token hay credential, kể cả trong `.env.example`, comment hoặc tài liệu.

- `.env.example`, `.env.qa.example`, `.env.production.example` chỉ chứa **placeholder**.
- Giá trị thật đặt trong `.env`, `.env.qa`, `.env.production` (đã nằm trong `.gitignore`).
- Không đưa dữ liệu khách hàng thật vào repo hoặc bản demo.
- Nếu lỡ commit bí mật, làm theo [SECURITY.md](SECURITY.md): coi như đã lộ và phải xoay (rotate).

## Báo lỗi

Mở [issue](https://github.com/AI20K-Build-Phase-Cohort-4/P-119/issues) kèm môi trường (OS, phiên bản Python/Node), các bước tái hiện và log lỗi. Lỗ hổng bảo mật thì **không** mở issue công khai — làm theo [SECURITY.md](SECURITY.md).

## Quy tắc ứng xử

Tham gia repo này đồng nghĩa với việc bạn đồng ý tuân thủ [Quy tắc ứng xử](CODE_OF_CONDUCT.md).
