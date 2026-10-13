# Trace thời gian nạp CSV và phân giải dữ liệu

Trace ghi từ lúc backend bắt đầu nhận phần CSV đầu tiên đến khi kết quả phân giải
được commit. Trace không đo thời gian người dùng duyệt hoặc xuất CSV.

Backend và worker ghi JSONL vào `data/trace/mdm-<pid>.jsonl`. Mỗi tiến trình có
file riêng. Có thể đặt `MDM_TRACE_DIR` trong `.env` để đổi thư mục, hoặc đặt
`MDM_TRACE_ENABLED=false` để tắt. Thư mục `data/` đã được Git bỏ qua.

Sau khi run hoàn tất, tạo báo cáo:

```powershell
.\.venv\Scripts\python.exe scripts\mdm_trace_report.py <run_id>
```

Nếu đổi `MDM_TRACE_DIR`, truyền cùng thư mục bằng `--trace-dir <directory>`.

Báo cáo được lưu tại `data/trace/reports/<run_id>/summary.txt` và
`timeline.json`. `summary.txt` liệt kê thời gian từng bước, thời gian chờ
worker, số bản ghi nguồn, số cặp ứng viên, số cặp cần review, số cặp được
chấp nhận vào nhóm tự động và số proposal `AUTO_READY`.

`auto_pair_count` đếm cặp có `triage_status=HIGH_CONFIDENCE_PROPOSAL`;
`auto_proposal_count` đếm nhóm `AUTO_READY`. Một nhóm có thể chứa nhiều cặp.
`review_pair_count` đếm cặp có `triage_status=REVIEW`. Các số này được đọc từ
PostgreSQL sau khi commit. Một `run.summary` thiếu nghĩa là chưa quan sát được
kết quả commit thành công; xem sự kiện `failed` và các bước `INCOMPLETE`.

Các thời gian của bước cha có thể bao gồm bước con. Không cộng các dòng thời gian
trong báo cáo thành tổng. Mốc tổng trong báo cáo là phần CSV đầu tiên mà backend
quan sát được đến sự kiện `run.summary`; nó có thể bao gồm khoảng chờ trước khi
người dùng bấm chạy. `QUEUE WAIT` được báo riêng.

Trace chỉ ghi ID, số lượng, trạng thái, thời gian và loại lỗi. Nó không ghi nội
dung CSV, giá trị từng trường, khóa R2 hoặc chuỗi kết nối database.
