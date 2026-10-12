# Duyệt cặp bằng hàng chờ giao diện

Khi Accept/Deny/Hoãn một cặp hoặc một phạm vi, giao diện nhận yêu cầu ngay và đưa vào hàng chờ riêng của phiên. Chỉ cặp hoặc phạm vi trùng yêu cầu đang chờ bị khóa; người dùng tiếp tục duyệt các cặp, Group và Subgroup khác. Bảng hàng chờ hiển thị trạng thái, lỗi và nút hủy yêu cầu chưa gửi.

Backend vẫn có một tác vụ ghi tại một thời điểm. Hàng chờ gửi tuần tự, chờ tác vụ duyệt hàng loạt hoàn tất rồi mới gửi yêu cầu tiếp theo. Trước mỗi lần gửi, giao diện đọc workflow mới nhất và kiểm tra quyền, phiên bản đánh giá; yêu cầu cho một cặp giữ revision đã quan sát để backend phát hiện quyết định thay đổi từ nơi khác. Không tự gửi lại yêu cầu ghi khi lỗi.

Phạm vi hàng loạt được chụp khi người dùng xác nhận. Backend tính lại số cặp đủ điều kiện khi thực hiện, chỉ xử lý cặp chưa duyệt/đang hoãn và giữ quyết định đã có. Accept thủ công tiếp tục chịu các ràng buộc của backend; Deny tiếp tục tạo cannot-link. `queue-decisions` chỉ cho phép giao diện tiếp nhận ý định khi tác vụ hàng loạt đang chạy, không cho phép ghi song song vào backend.

Hàng chờ tiếp tục chạy khi chuyển giữa các tab của phiên trong ứng dụng. Tối đa 100 yêu cầu chưa hoàn tất. Hàng chờ nằm trong bộ nhớ trình duyệt: cần giữ cửa sổ mở đến khi gửi xong; yêu cầu chưa gửi không được khôi phục sau khi tải lại trang hoặc đóng cửa sổ. Giao diện cảnh báo trước khi rời trang lúc còn yêu cầu chưa hoàn tất. Yêu cầu đã gửi thành tác vụ backend không bị hủy chỉ vì đóng trang.

Kiểm tra ngày 12/10/2026: 124 kiểm tra frontend, 37 kiểm tra backend liên quan, TypeScript và Docker build đạt. Kiểm tra trình duyệt trên bản triển khai đạt 8 tình huống: cặp khác vẫn dùng được; gửi tuần tự; tiếp tục qua tab phiên; thêm Group/Subgroup khi tác vụ hàng loạt đang chạy; không gửi hai tác vụ hàng loạt đồng thời; hủy yêu cầu chưa gửi; không có lỗi JavaScript. Các POST quyết định trong lượt QA trình duyệt được mô phỏng, không sửa quyết định của người dùng. Hợp đồng API thật được kiểm tra trong bộ kiểm tra backend.

Kết quả: [browser.json](../../artifacts/review-queue-20261012/browser.json), [ảnh giao diện](../../artifacts/review-queue-20261012/desktop.png).

## Giao diện duyệt gọn

Trang ưu tiên bốn số liệu tổng quan, Group bằng chứng, bộ lọc Subgroup, bảng 10 cặp/trang và nút duyệt. Các thẻ Group giữ số cặp và số còn cần duyệt; mô tả dài nằm trong hướng dẫn mở rộng và tooltip. Bộ lọc, chọn cặp và thao tác theo phạm vi được gộp vào một khối. Phạm vi đang chọn cùng điều kiện giữ quyết định cũ vẫn hiển thị trước khi xác nhận hàng loạt.

Công cụ đánh giá lại nằm trong phần mở rộng ở tiêu đề và giữ trạng thái khi dữ liệu tải xong hoặc chuyển tab phiên. Hàng chờ mặc định thu gọn thành một dòng số yêu cầu; mở để xem từng yêu cầu và hủy yêu cầu chưa gửi. Lỗi hàng chờ có thông báo bên ngoài phần mở rộng. Bằng chứng, metadata mô hình và lý do LLM mở tại từng dòng; đề xuất mô hình vẫn ghi rõ phạm vi synthetic. Không thay đổi logic quyết định, ràng buộc hay phân trang.

Kiểm tra bản triển khai: 124 kiểm tra frontend và build/TypeScript đạt; 10 kiểm tra giao diện desktop/mobile và 8 kiểm tra hàng chờ đạt. Bảng bắt đầu ở y=744,875px tại viewport 1440×1100; mobile 390×844 không tràn ngang toàn trang, bảng cuộn ngang trong vùng riêng. QA không lưu quyết định thật. [Kết quả giao diện](../../artifacts/pair-review-compact-20261012/browser.json), [ảnh desktop](../../artifacts/pair-review-compact-20261012/desktop-viewport.png), [ảnh mobile](../../artifacts/pair-review-compact-20261012/mobile.png).
