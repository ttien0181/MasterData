# Đề bài: Entity Resolution cho khách hàng và nhà cung cấp

## 1. Thực trạng

Cùng một khách hàng hoặc nhà cung cấp đang tồn tại dưới nhiều bản ghi có khác biệt nhỏ trên các hệ thống của Tập đoàn X. Điều này gây ra:

- Dữ liệu bị trùng lặp.
- Báo cáo 360 độ không chính xác.

## 2. Mục tiêu

Xây dựng một agent thực hiện **entity resolution** với các khả năng:

- Sinh khóa so khớp.
- Phát hiện các bản ghi trùng bằng phương pháp fuzzy matching và semantic matching.
- Đề xuất gộp các bản ghi thành một **golden record**.
- Giải thích lý do các bản ghi được đánh giá là trùng khớp.
- Duy trì bản ghi master sau khi hợp nhất.

## 3. Ràng buộc và yêu cầu

- **Human-in-the-loop (HITL):** Data Steward phải duyệt từng cặp gộp nghi ngờ trước khi hợp nhất.
- **Governance:** Giữ lại nguồn gốc dữ liệu, hỗ trợ tách bản ghi đã gộp và audit toàn bộ lịch sử gộp.
- **Độ chính xác:** Theo dõi các chỉ số precision và recall của quá trình matching.
- **Hiệu năng:** Tối ưu blocking để so khớp hiệu quả trên khối lượng dữ liệu lớn.
- **Chi phí:** Kiểm soát chi phí vận hành và xử lý dữ liệu.

## 4. Kiến trúc và công nghệ gợi ý

- **Matching:** LLM kết hợp với embedding.
- **Agent workflow:** LangGraph với quy trình:

  ```text
  Blocking → Pairwise matching → Clustering → Golden record
  ```

- **Thư viện entity resolution:** `recordlinkage`, `dedupe` hoặc `Splink`.
- **Vector database:** Lưu embedding của tên, địa chỉ và các trường phù hợp.
- **Kho dữ liệu:** Data warehouse.
- **Backend:** FastAPI.
- **Frontend:** React, tích hợp giao diện review.
- **Triển khai:** Docker và Cloud Run.

## 5. Phạm vi cơ bản

- Triển khai ứng dụng web.
- Hỗ trợ hai vai trò:
  - **Steward:** Xem xét và duyệt các đề xuất gộp.
  - **Viewer:** Xem dữ liệu và kết quả đã xử lý.
- Kết nối với nguồn dữ liệu.
- Agent phát hiện các cặp bản ghi có khả năng trùng.
- Hiển thị độ tin cậy và giải thích kết quả matching.
- Cho phép HITL duyệt gộp.
- Sinh golden record sau khi được duyệt.

## 6. Phạm vi nâng cao

- Xây dựng mô hình multi-agent gồm:
  - Agent embedding.
  - Agent rule.
  - Agent kết hợp kết quả để đưa ra quyết định.
- Đánh giá bằng precision, recall và F1 trên tập dữ liệu đã gán nhãn.
- Tối ưu chiến lược blocking.
- Học ngưỡng matching từ phản hồi của Data Steward.
- Hỗ trợ xử lý tách và gộp lại bản ghi.
