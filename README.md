# MasterOne

MasterOne là prototype khử trùng dữ liệu khách hàng: nạp nhiều CSV, dò trùng trong và giữa các nguồn, duyệt các item chưa chắc chắn, chọn giá trị chuẩn và xuất một CSV cuối cùng cho toàn bộ run. Backend FastAPI nằm trong `backend/`, frontend Next.js trong `frontend/`. Hướng dẫn chạy và luồng sử dụng ở [MDM_DEV.md](MDM_DEV.md). Repo bắt đầu từ template AI20K; tài liệu mẫu ban đầu vẫn nằm trong `docs/guide/`.

Technical Guidebook: <https://phoenix.note.transformerlabs.ai/technical-book>

## Thành phần trong repo

- **Backend** — package Python `masterone`, FastAPI, auth, MDM, SQLite và Alembic trong `backend/`.
- **Frontend** — ứng dụng Next.js trong `frontend/` với các tab Gộp, Không gộp, Cần review và Lịch sử CSV.
- **Docker và CI** — Dockerfile, `docker-compose.yml` và GitHub Actions kiểm tra cả backend lẫn frontend.
- **AI usage logging** — các hook trong `scripts/` và thư mục cấu hình công cụ tại root; log được gửi khi push theo thiết lập của template.
- **Technical Guidebook** — tài liệu mẫu của Ban tổ chức trong `docs/guide/`; các ví dụ đường dẫn `src/` trong sách mô tả template ban đầu.

## Yêu cầu

- `uv` để quản lý Python 3.11+ và môi trường ảo; không cần cài Python toàn hệ thống
- Node.js 22 trở lên và npm cho frontend (CI dùng Node.js 22)
- Git
- Docker — tuỳ chọn, chỉ cần nếu chạy `docker compose`

## Bắt đầu

### 1. Clone repo của đội

Khi đội được chốt, hệ thống tự sinh repo cho đội từ template này, nằm trong org
GitHub của khoá bạn đang học và đặt tên theo mã đội. Copy URL ở trang đội trên
Phoenix rồi clone về:

```bash
git clone https://github.com/<ORG-CỦA-KHOÁ>/<MÃ-ĐỘI>.git
cd <MÃ-ĐỘI>
```

Không cần `rm -rf .git`, `git init` hay `git remote add`: repo sinh từ template
đã bắt đầu bằng lịch sử riêng của đội và remote trỏ sẵn đúng chỗ. Chưa thấy repo
của đội thì báo BTC — repo tự tạo nằm ngoài org sẽ không được chấm.

### 2. Cài môi trường

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv -r requirements.txt
npm --prefix frontend ci
```

Chỉ cần cài lại frontend khi clone mới hoặc khi dependencies thay đổi. Xem [MDM_DEV.md](MDM_DEV.md) để thiết lập và chạy bằng PowerShell trên Windows.

### 3. Cấu hình biến môi trường

```bash
test -f .env || cp .env.example .env
```

Trên Windows PowerShell, dùng `if (-not (Test-Path .env)) { Copy-Item .env.example .env }`. Giữ `.env` hiện tại nếu file đã tồn tại.

Mở `.env` và điền `OPENAI_API_KEY`. Riêng `AI_LOG_API_KEY`, mỗi thành viên tự
tạo key riêng tại [dashboard Phoenix](https://phoenix.note.transformerlabs.ai/api-keys)
rồi thay vào chỗ `<get-your-api-key-from-dashboard-phoenix>` — giá trị trong
`.env.example` chỉ là placeholder, để nguyên thì log không vào được hệ thống chấm.

### 4. Cài hook ghi log AI

```bash
bash scripts/setup_hooks.sh                                    # Linux / macOS / Git Bash
powershell -ExecutionPolicy Bypass -File scripts\setup_hooks.ps1 # Windows PowerShell
```

Chạy một lần sau khi clone. Hook ghi lại prompt khi bạn dùng Claude Code, Cursor,
Codex CLI, Gemini CLI, Antigravity hoặc GitHub Copilot, và cài pre-push hook để
đẩy log lên server.

### 5. Chạy backend và frontend

Từ thư mục gốc repo, chạy migration lần đầu hoặc sau khi có revision mới, rồi mở hai terminal:

```powershell
uv run --python .venv python -m masterone.mdm.migrate
```

Terminal 1 — backend:

```powershell
uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001
```

Terminal 2 — frontend:

```powershell
npm --prefix frontend run dev
```

Mở ứng dụng tại <http://localhost:3000> và Swagger UI tại <http://localhost:8001/docs>. Backend cũng có thể chạy bằng `make run`; `make test` và `make lint` dùng để kiểm tra — xem `Makefile`.

Chạy các lệnh backend từ thư mục gốc repo để `.env`, `backend/alembic.ini` và `data/masterone.db` được tìm đúng vị trí. Xem [MDM_DEV.md](MDM_DEV.md) để chạy frontend, tạo tài khoản và thử luồng MDM.


## Cấu trúc thư mục

```
backend/
  pyproject.toml     Python package và dependency duy nhất
  alembic.ini        Cấu hình migration
  migrations/        Alembic revisions
  src/masterone/
    main.py          FastAPI entry point
    api/             HTTP routes
    auth/            User, session, CSRF
    db/              SQLAlchemy Base và session
    mdm/             Import, matching, Golden Record
    agents/           LangGraph agent mẫu
  tests/             pytest và CSV fixtures
frontend/            Next.js application
data/                SQLite local, không commit
scripts/             Hook ghi log AI + installer
docs/
  guide/             Technical Guidebook (nguồn của bản online)
  architecture_diagram.md
eval/                Kết quả evaluation
presentation/        Slide và video Demo Day
.claude/ .codex/ .cursor/ .gemini/ .agents/ .github/hooks/
                     Config hook cho từng công cụ
.github/workflows/   CI
Dockerfile           Multi-stage build
docker-compose.yml   Chạy backend bằng Docker
README_boilerplate.md  Khung README cho dự án của đội
```

## Technical Guidebook

| Chương | Nội dung | Thời gian |
|---|---|---|
| 1 | Lời mở đầu — mục tiêu, cách sử dụng | 15 phút |
| 2 | Khởi tạo dự án — clone, setup, git workflow | 4 giờ |
| 3 | Thiết kế kiến trúc — 3-tier, diagram, ADR | 6 giờ |
| 4 | LangGraph Agent — state, node, edge, tool, RAG | 8 giờ |
| 5 | FastAPI — routes, validation, error handling, streaming | 6 giờ |
| 6 | Giao diện — Next.js và Streamlit | 6 giờ |
| 7 | DevOps — Docker, CI/CD, deploy, logging | 6 giờ |
| 8 | Kiểm thử — unit test, integration test, RAGAS | 4 giờ |
| 9 | Demo Day — 10 deliverables, checklist | 2 giờ |
| 10 | Tài nguyên — khoá học, tài liệu, BMAD method | tham khảo |

Đọc online tại <https://phoenix.note.transformerlabs.ai/technical-book>: đăng
nhập bằng GitHub (đúng account đã được BTC mời vào org của khoá), chọn tab
**Technical Book** ở sidebar trái. Bản offline nằm trong `docs/guide/`, mở được
bằng bất kỳ markdown viewer nào.

## 10 deliverables cho Demo Day

| # | Deliverable | Vị trí | Template lo tới đâu |
|---|---|---|---|
| 1 | Source code | `backend/src/masterone/`, `frontend/` | Prototype đang phát triển |
| 2 | README | copy `README_boilerplate.md` thành `README.md` | Khung sẵn |
| 3 | Architecture diagram | `docs/architecture_diagram.md` | Khung sẵn |
| 4 | AI logs | LangSmith (3 biến môi trường) + auto AI usage logging | Cấu hình sẵn |
| 5 | Live URL | deploy lên Render/Vercel | CI/CD sẵn |
| 6 | Video demo | `presentation/` | Đội tự làm |
| 7 | Pitch deck | `presentation/` | Đội tự làm |
| 8 | Development journal | `JOURNAL.md` | Khung sẵn |
| 9 | Worklog | `WORKLOG.md` | Khung sẵn |
| 10 | Evaluation evidence | `eval/` | Đội tự làm |

## Tech stack

| Lớp | Công nghệ |
|---|---|
| Agent | LangGraph + LangChain 0.3 |
| Backend | FastAPI + Uvicorn, package `masterone` |
| LLM | OpenAI, mặc định `gpt-4o-mini` (đổi trong `backend/src/masterone/config.py`) |
| Giao diện | Next.js trong `frontend/` |
| Lint / test | ruff + pytest 8 |
| DevOps | Docker + GitHub Actions |
## AI usage logging

Mọi prompt được ghi vào `.ai-log/session.jsonl` và tự động gửi lên grading server
ở bước pre-push.

| Công cụ | Cấu hình | Thời điểm ghi |
|---|---|---|
| Claude Code | `.claude/settings.json` | mỗi prompt (`UserPromptSubmit`) |
| Cursor | `.cursor/hooks.json` | mỗi prompt và khi dừng |
| OpenAI Codex CLI | `.codex/hooks.json` | mỗi prompt và khi dừng |
| Gemini CLI | `.gemini/settings.json` | mỗi lượt agent chạy |
| GitHub Copilot | `.github/hooks/hooks.json` | mỗi prompt và cuối session |
| Antigravity IDE | `.agents/hooks.json` | mỗi prompt, kèm lần quét lại lúc `git push` |

Dùng ChatGPT hay công cụ web khác thì log thủ công:

```bash
bash scripts/_pyrun.sh scripts/log_manual.py --tool chatgpt --prompt "What you asked"
```

## Đóng góp

Repo này là open source. Đọc [CONTRIBUTING.md](CONTRIBUTING.md) trước khi mở PR.

Nội dung trong `docs/guide/` là nguồn của Technical Book và được đồng bộ lên bản
online, nên mọi thay đổi ở đó cần review của
[@AI20K-Build-Phase/book-maintainers](https://github.com/orgs/AI20K-Build-Phase/teams/book-maintainers)
— xem [.github/CODEOWNERS](.github/CODEOWNERS).

Báo lỗ hổng bảo mật theo [SECURITY.md](SECURITY.md), đừng mở public issue.

## License

[MIT](LICENSE) — dùng tự do cho mục đích giáo dục.

123456
