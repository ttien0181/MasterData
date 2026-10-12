# Tối ưu duyệt hàng loạt và đo cluster

1. Chuyển giao dịch ghi từ từng cặp sang lô giới hạn 100 cặp. Giữ request ID của từng cặp, audit và revision; lỗi ngoài dự kiến rollback cả lô, lô đã commit có thể replay.
2. Trong mỗi lô, đọc một lần run/actor/pairs/replay, cấu hình engine, bản ghi nguồn, veto và xung đột; dùng chung đường kiểm tra và ghi quyết định với thao tác đơn. Không bỏ ràng buộc để tăng tốc.
3. Lưu processed và applied/blocked/stale cùng transaction ghi của lô. Lưu checkpoint và counts trong operation để tiếp tục sau lỗi mà không đếm lại lô đã ghi. Cập nhật tiến độ callback tương thích hiện có.
4. Đo cluster trước khi thay đổi bằng snapshot chỉ đọc của phiên người dùng và workload tổng hợp có nhóm lớn. Tối ưu điểm nóng đã đo; kiểm chứng đầu ra clusters/conflicts và guard budgets không đổi.

Kiểm tra hồi quy: rollback giữa lô, lỗi sau commit trước callback, replay legacy, stale/blocked/auto-only, Deny/cannot-link, request mismatch, lease/cancel fencing, quyền thay đổi, query/commit theo số lô; profile và benchmark trên PostgreSQL QA riêng. Cập nhật Docker QA sau khi kiểm thử/build đạt, giữ cấu hình và dữ liệu người dùng.
