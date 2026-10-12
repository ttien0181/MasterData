# Tự chuyển từ duyệt cặp sang các nhóm bản ghi

Thay đổi ngày 2026-10-11 theo lựa chọn: **Accept là xác nhận của người duyệt**.

## Hành vi

- Bỏ nút “Hoàn tất duyệt & tạo cluster”.
- Sau quyết định cuối cùng, backend tự đóng băng bản duyệt và xếp hàng tạo cluster. Bulk lưu marker `auto_cluster_pending` cùng transaction hoàn thành; supervisor tiêu thụ marker khi task slot đã được giải phóng, kể cả sau restart. Bước seal snapshot không giữ khóa attempt đã hoàn thành nên không chặn heartbeat. Không phụ thuộc việc trình duyệt còn mở.
- Phiên cũ đã duyệt xong được đối chiếu qua mutation `POST /auto-cluster` khi mở trang. `GET /workflow` vẫn chỉ đọc.
- Khi cluster được xếp hàng, giao diện tự chuyển từ Phân loại cặp sang Các nhóm bản ghi, hiển thị tiến độ và tự đọc kết quả khi hoàn thành.
- Cặp đang hoãn/chưa giải quyết và bulk đang chạy vẫn chặn chuyển bước. Cặp bị quy tắc bắt buộc giữ riêng không cần Accept cưỡng ép.
- Mỗi decision revision chỉ có một snapshot/operation cluster. Poll, refresh và nhiều cửa sổ không xếp hàng trùng.
- Khi người dùng chủ động mở lại duyệt, hệ thống đợi thay đổi duyệt đầu tiên, không tự đóng lại ngay.

## Ý nghĩa Accept và các ràng buộc

Snapshot mới ghi policy `accepted-links-confirm-component-v1`. Chỉ `user_decision=MERGE` trong bản duyệt đã đóng băng mới là xác nhận thủ công; MERGE do quy tắc tự động không được gán quyền này.

Các liên kết Accept xác nhận cùng đối tượng theo tính bắc cầu. Trong thành phần đã được xác nhận, không yêu cầu lại bằng chứng tự động cho từng bridge và cho phép bản ghi trùng cùng nguồn. Các thành phần do quy tắc tự ghép vẫn giữ kiểm tra bằng chứng và chính sách unique-source.

Mọi cặp thành viên vẫn được kiểm tra theo hard constraints của engine đã chọn: xung đột định danh, khác loại đối tượng, quyết định Deny/cannot-link. Giới hạn kích thước nhóm và budget không đổi. Thông tin engine/config, checksum artifact, audit, revision và lịch sử snapshot vẫn được giữ.

Một snapshot cũ gặp xung đột được tạo lại dưới policy mới: ghi audit thay đổi ngữ cảnh policy, tăng revision, đánh dấu xung đột cũ `SUPERSEDED_BY_POLICY`, giữ nguyên các quyết định đã duyệt và snapshot lịch sử.

## Xung đột và lỗi

Xung đột lặp với cùng ID được gom thành một xung đột để PostgreSQL không lỗi khóa chính. Danh sách pair IDs liên quan được đọc bằng một truy vấn index cho mỗi thành phần, thay vì truy vấn/giải mã JSON từng tổ hợp.

Nếu còn ràng buộc chặn một số liên kết, tab Các nhóm bản ghi vẫn mở và hiển thị số xung đột đầy đủ, cùng **nhóm dự kiến** đã vượt qua kiểm tra. Nhóm dự kiến có phân trang và xem thành viên; các liên kết bị chặn chưa được ghép. Dữ liệu này được đọc từ artifact đã kiểm chứng checksum/hash và snapshot revision hiện tại. Không dùng nhóm dự kiến làm membership đã xuất bản, golden record hay export.

Giao diện duyệt nhóm đã được bổ sung bảng so sánh và lưu lựa chọn Golden dự thảo theo [CLUSTER_REVIEW_UX.vi.md](CLUSTER_REVIEW_UX.vi.md). Dự thảo không phải Golden đã xác nhận và không được xuất khi còn xung đột nhóm.

Khi hết xung đột, membership được xuất bản đầy đủ và bước golden mở như trước. Khi worker thất bại sau các lần thử tự động, trang báo lỗi và có “Thử lại tạo nhóm”; thao tác này tái sử dụng snapshot hiện tại, không yêu cầu Accept lại và không tạo thêm operation/snapshot.

## Kiểm chứng

Các kiểm tra và bằng chứng runtime nằm ở `artifacts/session-workflow/20261011-workflow-01/auto-cluster-20261011/`. Bao gồm backend/worker/frontend Docker, luồng browser Accept → tự chuyển trang → nhóm, bulk Deny khi không có browser, audit/request replay, chạy lại lỗi, hard constraints, bản xem trước không xuất bản, scope và checksum.

- 127 kiểm thử backend liên quan và 93 kiểm thử frontend đạt; Ruff, lint, TypeScript và Docker build đạt.
- Browser thực tế: Accept 15 cặp → tự chuyển sang Các nhóm bản ghi → 1 cluster gồm 6 bản ghi; tải lại vẫn chỉ có một operation cluster, không có lỗi JavaScript.
- Bulk Deny chạy không có browser → tự tạo 6 nhóm một thành viên; audit của 15 quyết định Accept được giữ đầy đủ.
- Đã đối chiếu phiên `21c9a723-8aa0-44f5-9083-dd4edc99c6ef`: giữ nguyên quyết định duyệt, cluster revision 2, có 108 nhóm dự kiến và 3.546 xung đột `CLUSTER_SIZE_LIMIT`. Các nhóm dự kiến không vượt giới hạn 20 thành viên; chưa xuất bản membership hay mở golden khi còn xung đột.

Không yêu cầu migration hay thay đổi cấu hình runtime. Chỉ cập nhật ứng dụng QA `masterone-workflow-live`; giữ nguyên database, bucket và cấu hình hiện tại.
