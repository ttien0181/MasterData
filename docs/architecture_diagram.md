# Kiến trúc MasterOne MVP

Sơ đồ này mô tả ứng dụng đang chạy trong repo, không bao gồm LangGraph agent mẫu tại `backend/src/masterone/agents/`. MVP xử lý các bản ghi khách hàng `PERSON` trong **từng lần nạp (run) độc lập**.

## Components

```mermaid
flowchart LR
    User[Data Steward] --> Browser[Next.js frontend]
    Browser -->|HTTP JSON, CSV; session cookie và CSRF| API[FastAPI API]

    subgraph Backend[Backend Python]
        API --> Auth[Auth và kiểm tra quyền sở hữu run]
        API --> Import[Kiểm tra CSV, tạo run và chuẩn hóa]
        API --> Review[Danh sách, quyết định review, preview, xuất CSV]
        API --> Queue[ResolutionTask trong DB]
        Queue --> Worker[Worker một tiến trình]
        Worker --> Pipeline[Blocking → matching/policy → clustering]
        Review --> Explain[Giải thích review theo yêu cầu]
    end

    Auth --> Store[(SQLite qua SQLAlchemy)]
    Import --> Store
    Queue --> Store
    Pipeline --> Store
    Review --> Store
    Explain --> Store
    Pipeline -. đối chiếu địa chỉ khi bật cờ .-> Gemini[Gemini API]
    Explain -. thống kê tín hiệu khi bấm nút .-> Gemini
    Review -->|CSV snapshot| Browser
    Migrations[Alembic migrations] --> Store
```

Frontend ở `frontend/`; API, auth, worker và pipeline ở `backend/src/masterone/`. SQLite mặc định là `data/masterone.db`, không phải Supabase đang chạy. API chỉ trả run của chủ tài khoản. Các lựa chọn trường chưa xuất được giữ trong `localStorage` của trình duyệt; khi xem trước và xác nhận xuất, backend kiểm tra lại và lưu CSV snapshot trong database để tải từ Lịch sử.

## Luồng dữ liệu

```mermaid
flowchart TD
    Files[1–10 CSV: CRM, ERP, BILL] --> Validate[Đọc UTF-8, kiểm tra cột, ID, giới hạn file]
    Validate --> Normalize[Chuẩn hóa trường và cờ chất lượng]
    Normalize --> Records[(Source records gắn với run)]
    Records --> Blocking[Sinh candidate pairs trong toàn run]
    Blocking --> Score[So sánh tín hiệu, tính điểm rule và áp policy]
    Score --> Semantic{Đối chiếu địa chỉ bật?}
    Semantic -->|Có; chỉ một số cặp review| Gemini[Gemini: tín hiệu địa chỉ]
    Semantic -->|Không| Cluster[Kiểm tra xung đột và tạo cụm]
    Gemini --> Cluster
    Cluster --> Merge[Chờ gộp]
    Cluster --> Review[Cần review]
    Cluster --> Separate[Giữ riêng]
    Review -->|Người dùng quyết định| Merge
    Review -->|Không gộp: từ chối cặp chưa chắc chắn| Split[Giữ các liên kết khớp an toàn]
    Split --> Merge
    Split --> Separate
    Merge --> Choices[Chọn giá trị khác nhau]
    Choices --> Preview[Xem trước và kiểm tra điều kiện xuất]
    Separate --> Preview
    Review -->|Chưa duyệt: giữ riêng khi xác nhận cảnh báo| Preview
    Preview --> Export[CSV kết quả và bản lưu trong Lịch sử]
```

**Ranh giới của pipeline:** dữ liệu được so khớp cả trong cùng file/nguồn lẫn giữa các file của một run; không so với các run cũ. `entity_id` trong dữ liệu đánh giá không đi vào quyết định. Matching và phân cụm chủ yếu là rule; điểm không phải xác suất đã hiệu chỉnh. Gemini giải thích khi người dùng yêu cầu, và đối chiếu địa chỉ chỉ khi `SEMANTIC_ADDRESS_ENABLED=true`; kết quả Gemini không tự tạo quyết định gộp.

**Đầu ra:** mỗi bản ghi nguồn xuất hiện đúng một lần trong CSV kết quả, có thể nằm trong cụm gộp hoặc được giữ riêng. Bản xem trước và bản xuất là hai bước riêng; nếu dữ liệu/quyết định thay đổi hoặc preview hết hạn thì cần tạo preview mới. Lịch sử lưu các CSV snapshot và hỗ trợ xem trước/sau, tải lại, và tách bản ghi từ một bản đã xuất.
