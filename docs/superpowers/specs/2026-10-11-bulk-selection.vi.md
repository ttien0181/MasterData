# Tích chọn cặp và nhóm bản ghi

Thiết kế theo mẫu bảng có checkbox và thanh thao tác hàng loạt của [Carbon Design System](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines).

- Checkbox đầu bảng chọn **trang đang hiển thị**; trạng thái gạch ngang thể hiện chọn một phần trang.
- Khi có lựa chọn, hiển thị số lượng và nút **Chọn toàn bộ kết quả** để mở rộng sang các trang khác trong phạm vi hiện tại. Có thể bỏ chọn từng dòng hoặc bỏ chọn tất cả.
- Giữ lựa chọn khi chuyển trang hoặc chuyển tab trong cùng phiên. Đổi bộ lọc hoặc phiên bản quyết định/Golden sẽ xóa lựa chọn cũ.
- Thanh **Accept đã chọn** nằm dưới phân trang. Giữ nguyên các nút Accept/Deny, đối chiếu, so sánh nhóm, lưu Golden và chuyển sang Golden record trước đây.
- Checkbox nhóm độc lập với nút mở nhóm để so sánh. Dòng đang mở và dòng tích chọn có trạng thái riêng.

Accept cặp kiểm tra trước số lượng đủ điều kiện từ backend, sau đó đóng băng đúng ID/phiên bản đã chọn cho worker. Phạm vi toàn bộ giữ bộ lọc Group, Subgroup, trạng thái và danh sách loại trừ. Cặp bị chặn được giữ nguyên; hiển thị tiến độ và kết quả áp dụng/bị chặn/đã thay đổi.

Theo lựa chọn của người dùng, Accept nhóm **chỉ xác nhận Golden đã lưu** của các nhóm đã được chốt và đủ điều kiện. Không tự lưu các thay đổi chưa lưu trong bảng so sánh. Nhóm dự kiến của phiên còn xung đột chỉ có thể xem và lưu dự thảo; thanh thao tác giải thích vì sao chưa được xác nhận. Worker kiểm tra từng Golden và hiển thị kết quả; không tự chốt phiên hay xuất dữ liệu.

Tối đa 1.000 ID chọn riêng lẻ hoặc loại trừ mỗi lần, phù hợp giới hạn API. Chọn toàn bộ qua bộ lọc không bị giới hạn bởi số trang hoặc giới hạn này. Backend giữ kiểm tra chủ sở hữu, quyền, CSRF cho thao tác ghi, phiên bản và các ràng buộc hiện có. Phạm vi loại trừ được đưa vào dấu kiểm tra của yêu cầu để bảo đảm phát lại đúng thao tác.

Kiểm chứng: các test frontend bao phủ chọn qua nhiều trang, checkbox ba trạng thái, Group/Subgroup, loại trừ, thay đổi bộ lọc/phiên bản, cảnh báo xung đột và xác nhận nhóm. Test backend chạy worker thật với phạm vi chọn riêng lẻ và toàn bộ trừ một phần; kiểm tra dữ liệu không chọn được giữ nguyên. Script `frontend/scripts/qa-bulk-selection.mjs` kiểm chứng trên trình duyệt với các phiên dữ liệu giả ở Docker QA và chụp bố cục desktop/mobile.
