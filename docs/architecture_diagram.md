# Sơ đồ kiến trúc MasterOne

```mermaid
flowchart TB
    Steward[Data Steward] --> UI[Next.js · frontend/]
    UI -->|Đăng ký, đăng nhập, MDM API| Routes[FastAPI · backend/src/masterone/api/]
    Routes --> Auth[auth/ · phiên, CSRF, quyền]
    Routes --> MDM[mdm/ · CSV, matching, review, draft]
    Auth --> DB[db/ · SQLAlchemy session]
    MDM --> DB
    DB --> SQLite[(data/masterone.db)]
    Alembic[backend/migrations/ · Alembic] --> SQLite
    TemplateAgent[agents/ · luồng mẫu độc lập] --> LLM[services/llm.py]
```

Các file CSV chỉ được giữ dưới dạng source records đã nhập. Matcher tạo candidate; quyết định của người duyệt và bản nháp Golden Record được lưu riêng. Frontend chỉ hiển thị import thuộc tài khoản đang đăng nhập; backend kiểm tra quyền trước khi trả dữ liệu.
