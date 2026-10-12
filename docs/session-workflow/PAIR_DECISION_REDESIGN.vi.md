# Thiết kế lại trang phân loại cặp bản ghi — 11/10/2026

Trang `/app/sessions/[runId]/decisions` phục vụ duyệt các cặp trước khi tạo cluster. Người dùng cần hiểu Group/Subgroup, đọc hai bản ghi nguồn và quyết định theo phạm vi chính xác.

## Bố cục và ý nghĩa

- Bốn chỉ số trên toàn phiên: tổng cặp, chờ duyệt, được xét ghép, không xét liên kết; thanh tiến độ quyết định.
- Group hiển thị thành thẻ có mô tả và số cặp. A có neo nhận dạng; C cần thêm kiểm tra; D có mâu thuẫn. Group thực tế khác vẫn hiển thị theo kết quả của phiên.
- Subgroup hiển thị tên bằng chứng dễ đọc, số cặp và nút chọn riêng. Bộ lọc dùng phân nhóm chính: một cặp được đếm một lần.
- `null/null` được trình bày là “Chưa phân nhóm” / “Chưa xác định Subgroup”; bộ lọc `UNCLASSIFIED` chỉ chọn dữ liệu NULL, không lẫn với “Tất cả”. Không gán nhãn A/C/D giả cho cặp chưa đủ bằng chứng hoặc bị chặn trước bước phân nhóm.
- Bảng có tên, mã và nguồn của hai bản ghi, điện thoại, phân nhóm, điểm quy tắc, quyết định và thao tác. Phân trang bằng cursor, 10 cặp mỗi trang; điểm quy tắc có thể âm và không phải xác suất.
- Đối chiếu hai bản ghi trong hộp thoại có dữ liệu gốc/chuẩn hóa, tô khác biệt, lọc chỉ hiện khác biệt và bằng chứng theo trường. Giải thích AI chỉ chạy khi người dùng yêu cầu.
- CSS theo theme hiện có, thẻ xếp lại trên điện thoại; bảng cuộn trong vùng riêng, không làm tràn toàn trang.

Thiết kế danh sách cặp, đối chiếu bản ghi và xác nhận/từ chối tham khảo quy trình [Matching Proposals của Ataccama MDM](https://docs.ataccama.com/mdm/latest/matching-proposals.html). Điểm và nhãn của dự án giữ theo engine hiện có.

## Quyết định và phạm vi

Accept ghi `MERGE`: cặp được xét ghép khi hoàn tất duyệt, vẫn chịu kiểm tra ràng buộc. Deny ghi `NON_MATCH`: tạo cannot-link, ngăn hai bản ghi cùng cluster kể cả qua liên kết gián tiếp. “Giữ riêng liên kết” (`KEEP_SEPARATE`) vẫn có trong phần thao tác khác và được giải thích khác với Deny. Hoãn (`DEFER`) tiếp tục chặn hoàn tất duyệt.

Accept/Deny có ở bốn mức: toàn phiên, Group, Subgroup, từng cặp. Các mức hàng loạt dùng snapshot toàn phạm vi với `status: ALL`, bao gồm các trang chưa xem. Hộp xác nhận hiển thị số cặp, khả năng áp dụng và lý do có thể sửa; hủy không ghi quyết định. Accept mặc định dùng quyền xét thủ công (`auto_only: false`); có tùy chọn giới hạn chỉ cặp đủ điều kiện tự động. Cặp bị ràng buộc chặn sẽ giữ nguyên.

Quyền, chủ sở hữu, CSRF, request ID, revision và audit vẫn được backend kiểm tra. Không tạo cluster khi đọc trang hoặc ghi quyết định; nút “Hoàn tất duyệt & tạo cluster” sử dụng workflow và blockers hiện có.

Tổng quan và xem trước đọc bản ghi theo lô 512, thay vì truy vấn từng cặp. Trang 10 cặp chỉ đọc veto của các liên kết trên trang, với hai cột ID; không tải payload toàn bộ cặp đã Deny. Theo dõi thao tác hàng loạt có thử lại khi lỗi mạng, và tiếp tục sau khi tải lại/chuyển trang trong lúc đang xử lý.

## Kiểm chứng

- Frontend: 86 kiểm thử / 15 file đạt; TypeScript đạt; Docker build production Next.js đạt.
- Backend: 68 kiểm thử liên quan đến đọc cặp, review, bulk, API, finalize, workflow cluster và ràng buộc cluster đạt; Ruff đạt.
- Review độc lập phát hiện hai vấn đề về khôi phục theo dõi bulk và tải veto khi phân trang. Đã sửa, bổ sung kiểm thử xác nhận lỗi trước sửa và đạt sau sửa; rà soát lại không còn vấn đề quan trọng trong phạm vi này.
- Bản Docker frontend/backend/worker đã cập nhật ở `masterone-workflow-live`, giữ cấu hình runtime hiện có; hash ba module backend đã đối chiếu với source. Các container ứng dụng healthy.
- Phiên người dùng `21c9a723-8aa0-44f5-9083-dd4edc99c6ef`: 17.334 cặp, 15.220 chờ duyệt, 2.114 giữ riêng theo quy tắc. Kiểm tra trang 10 cặp, trang sau/trước, lọc NULL, đối chiếu, hủy xem trước Accept/Deny; decision/workflow revision không đổi. Không có lỗi JavaScript hay tràn trang ở rộng 390 px. Thống kê phân nhóm đầy đủ mất khoảng 8 giây trên môi trường QA này.
- Phiên thử riêng `2cd8c6f9-29cd-4be7-8f86-a177c40f5e1d`: upload tự phân loại; Accept từng cặp và Group, Deny Subgroup và toàn phiên; backend trả 409 khi thử Accept liên kết đã Deny. Sau Deny toàn bộ 15 cặp, có 15 negative edge; chỉ sau finalize mới tạo 6 cluster singleton cho 6 bản ghi. Tài khoản thử đã vô hiệu hóa.
- Ảnh desktop/mobile/dark dùng bản ghi được ẩn danh sau khi các kiểm tra dữ liệu thật hoàn tất.
- Hộp đối chiếu và xác nhận lồng nhau đã kiểm tra: hủy xác nhận trở về đối chiếu, Escape đóng hộp, hộp nằm trong viewport điện thoại; ảnh đối chiếu dùng dữ liệu ẩn danh.

Chứng cứ: `artifacts/session-workflow/20261011-workflow-01/decision-redesign-20261011/` gồm deployment, browser-check, mutation-check và ảnh chụp. Đây là kiểm chứng cho thay đổi trang phân loại, không thay thế baseline toàn repository.
