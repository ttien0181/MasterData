# Hiệu năng duyệt cặp hàng loạt và tạo cluster — 2026-10-11

Đã triển khai cả bốn bước của [kế hoạch](../superpowers/plans/2026-10-11-bulk-review-performance.vi.md) lên Docker QA `masterone-workflow-live` tại `http://127.0.0.1:13018`. Backend và worker dùng cùng bản mới; frontend và cấu hình runtime được giữ nguyên. Không cần migration.

## 1. Ghi quyết định theo lô

`workflow_bulk.run_bulk_decisions` ghi tối đa 100 cặp trong một transaction. Mỗi quyết định áp dụng vẫn có revision cặp, revision phiên, audit event và operation/request ID riêng như thao tác duyệt đơn.

Scope Parquet được đóng băng tại lúc gửi yêu cầu, xác thực checksum và số hàng trước khi ghi. Checkpoint gồm vị trí đã xử lý, các bộ đếm và checksum scope được commit cùng quyết định. Khi chạy tiếp, worker bỏ qua phần đã commit. Operation cũ chưa có checkpoint vẫn replay theo request ID của từng cặp, tránh ghi audit hoặc tăng revision hai lần.

Lỗi ngoài dự kiến rollback cả lô đang xử lý; các lô đã commit được giữ lại. Quyền người duyệt được kiểm tra lúc thực thi. Worker kiểm tra lease, attempt và yêu cầu hủy trước lô và trước commit; việc ghi vẫn giữ khóa phiên chung với thao tác duyệt đơn.

## 2. Tái sử dụng dữ liệu và kiểm tra trong lô

Mỗi lô tải pairs, replay operations, actor, cấu hình engine, bản ghi nguồn và cannot-links theo scope một lần. Deny tải thêm các negative edges cần cập nhật; quyết định không ghép tải các xung đột mở. Mọi thao tác vẫn đi qua `decide_pair` và các điều kiện phía server.

Accept kiểm tra eligibility một lần và tái sử dụng kết quả khi dựng response; sau khi ghi, `auto_allowed` được cập nhật đúng theo quyết định mới. Deny cập nhật tập veto dùng chung trong lô. Không thay đổi chính sách Group D, auto-only, hard constraints hoặc cannot-link.

## 3. Tiến độ đầy đủ và bền vững

`processed`, `total`, `applied`, `blocked`, `stale` được ghi sau mỗi lô, cùng transaction với quyết định. Giao diện hiện bộ đếm khi operation còn `RUNNING`; bộ đếm khi chạy tiếp bao gồm cả các lô trước lỗi.

Kiểm tra Playwright trên giao diện thật ghi nhận:

- Đang chạy: **600 / 17.334**, áp dụng **532**, bị chặn **68**, đã thay đổi trước đó **0**.
- Hoàn thành: **17.334 / 17.334**, áp dụng **15.220**, bị chặn **2.114**, đã thay đổi trước đó **0**.
- Không có JavaScript page error.

## 4. Đo cluster trước khi tối ưu

Profile bản cũ xác định hai điểm tốn thời gian: sao chép/giải mã payload JSON lớn trong SQLite DecisionStore và kiểm tra lại hard constraints của các cặp thành viên qua nhiều lần union.

Bản mới chỉ đưa các trường guard sử dụng vào spool (`id`, endpoints, weight, comparisons và quyết định hiệu lực), đồng thời cache tối đa 4.096 kết quả hard constraints trong một lần dựng cluster. Evidence trong artifact đã seal vẫn được giữ đầy đủ. Thứ tự union, kiểm tra bridge, budget, cannot-links và quy tắc xuất bản không đổi.

| Phép đo | Trước | Sau | Kết quả |
| --- | ---: | ---: | --- |
| Bulk Accept 17.334 cặp, tổng từ gửi yêu cầu tới hoàn thành | Khoảng 492 giây | 38,5 giây | Khoảng 12,8 lần nhanh hơn |
| Hàm cluster, snapshot thực tế 1.651 bản ghi / 17.334 cặp | 5,22 giây | 3,04 giây | Giảm khoảng 42% |
| Hàm cluster, 100 nhóm × 20 bản ghi / 19.000 cặp | 3,06 giây | 2,12 giây | Giảm khoảng 31% |

Bulk bản mới đo trên bản sao riêng có cùng số lượng và dữ liệu cặp với phiên người dùng: tiếp nhận yêu cầu 7,057 giây, worker và theo dõi hoàn thành 31,410 giây. Có đầy đủ 15.220 audit và 15.220 decision revision riêng. Đây là một lần đo QA so với operation cũ đã quan sát, không phải benchmark nhiều lần với tải hệ thống cố định. Phiên nguồn không bị thay đổi bởi benchmark; các tài khoản thử nghiệm được vô hiệu hóa sau kiểm tra.

Phép đo cluster so sánh bản trước/sau trên cùng input trong cùng container, có overhead của cProfile; không bao gồm thời gian tải artifact, đóng băng review, lưu database và xuất bản cluster. SHA-256 của toàn bộ kết quả, gồm clusters, conflicts và guard metrics, giống nhau trước/sau trên cả hai workload.

Snapshot thực tế vẫn trả về **15.220 xung đột `CLUSTER_BRIDGE_MISSING_EVIDENCE`**, nên không xuất bản cluster. Đây cũng là kết quả của bản cũ: quyết định Accept không bỏ qua yêu cầu bằng chứng liên kết của bước cluster. Workload tổng hợp có bằng chứng đầy đủ trả về 100 cluster, không có xung đột. Phép tối ưu hiệu năng không sửa chính sách này.

## Kiểm chứng và bằng chứng

- **101 kiểm thử backend đạt** trong 58,50 giây: bulk, review, API, pair reads, finalize, clusters, cluster guards, safe clustering và job queue.
- Kiểm thử lỗi giữa lô, lỗi sau commit trước callback, chạy tiếp nhiều lô với bộ đếm hỗn hợp, replay operation cũ, stale revision, quyền thay đổi, auto-only và fencing trước commit.
- Ruff đạt trên ba module thay đổi và hai file kiểm thử.
- Luồng trực tiếp upload → tự phân loại → Accept từng cặp/Group → Deny Subgroup/toàn bộ → finalize đạt. Dữ liệu thử 6 bản ghi và 15 cặp giữ 15 negative edges, tạo 6 cluster singleton sau Deny; không tạo cluster trong lúc duyệt cặp.
- Docker backend, worker, frontend và database healthy sau triển khai; hash module cài trong container khớp source.

Bằng chứng nằm tại `artifacts/session-workflow/20261011-workflow-01/bulk-performance-20261011/`: `bulk-benchmark.json`, `browser-progress.json`, `cluster-comparison.jsonl`, `mutation-check.json`, `deployment.json` và `verification.json`. Các script đo và kiểm tra được lưu cùng thư mục. Không gọi LLM trả phí trong phép thử.
