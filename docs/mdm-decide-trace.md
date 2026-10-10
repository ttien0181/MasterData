# Trace chi tiết bước DECIDE

`pipeline.decide` đo thời gian thực của toàn bước. Các dòng dưới đây chia nhỏ công việc;
`score_read`, `hydrate`, `worker_submit`, `worker_wait`, `policy_compute` và `route`
là tổng thời gian của các batch, được ghi một lần sau khi xử lý xong. Mỗi batch tối đa
2.048 cặp theo cấu hình SCORE hiện tại. Log chỉ chứa ID run, số lượng và thời gian.

| Step | Ý nghĩa |
| --- | --- |
| `pipeline.decide.score_read` | Đọc và giải mã các batch từ file điểm Parquet. |
| `pipeline.decide.hydrate` | Lấy bản ghi ở hai đầu mỗi cặp từ snapshot. Chỉ có ở `StageRunner`; nhánh `memory` đã giữ bản ghi trong RAM. |
| `pipeline.decide.worker_submit` | Gửi batch sang các tiến trình xử lý policy, nếu chạy song song. |
| `pipeline.decide.worker_wait` | Thời gian tiến trình chính bị chặn khi chờ kết quả từ worker. |
| `pipeline.decide.policy_compute` | Tổng thời gian tính policy. Ở `StageRunner` chạy song song, đây là tổng thời gian của các worker và có thể chồng lấn với `hydrate`; không cộng vào `pipeline.decide`. |
| `pipeline.decide.route` | Đếm và bỏ cặp thiếu bằng chứng, tạo ID và giữ các cặp còn lại. |
| `pipeline.decide.semantic` | Bổ sung xử lý địa chỉ bằng AI nếu bật. Nhánh `memory` chạy bước này sau timer `pipeline.decide`; `StageRunner` chạy bên trong timer. |
| `pipeline.decide.json_encode` / `json_decode` | Chuyển trường cấu trúc sang JSON rồi giải mã khi đọc lại. Chỉ có ở `StageRunner` dùng danh sách quyết định. |
| `pipeline.decide.artifact_write` / `artifact_read` | Sắp xếp, ghi và đọc file quyết định Parquet. Chỉ có ở `StageRunner`. |
| `pipeline.decide.spool_seal` | Chốt kho SQLite tạm nếu bật cluster safety. |

Hai nhánh có cùng thuật toán policy nhưng ranh giới timer khác nhau. Các mốc thời gian
song song và thời gian của bước cha không cộng trực tiếp với nhau. Muốn so tốc độ xử lý
policy, đối chiếu `policy_compute` và lưu ý số worker; muốn so tốc độ toàn bộ, dùng
`pipeline.total`.

Tạo báo cáo theo run ID bằng `python scripts/mdm_trace_report.py <run_id>`.
