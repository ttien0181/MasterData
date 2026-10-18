# Cách gọi tên trên giao diện

Áp dụng cho luồng hiện tại của nhánh feature/feature_5. Câu chữ tham khảo nhánh
feature/feature6 tại commit 9b7095a của Liine1310h, nhưng phải mô tả đúng hành vi
của luồng hiện tại.

| Đối tượng | Cách gọi |
| --- | --- |
| Một dòng dữ liệu nguồn | Dòng thông tin |
| Hai dòng được đem so sánh | Cặp nghi trùng |
| Một lần nạp và xử lý dữ liệu | Đợt xử lý |
| Hàng đợi các cặp chưa xử lý | Cặp cần xem xét |
| Danh sách đề xuất tự động và các cặp được chọn hoặc xác nhận | Chờ gộp |
| Các dòng được dự kiến gộp lại | Nhóm gộp |
| Giá trị hợp nhất đang xem trước | Hồ sơ chuẩn dự kiến |
| Nhật ký các thao tác | Lịch sử thao tác |

## Số liệu tổng quan

Biểu đồ Phân loại ban đầu đếm các dòng thông tin riêng biệt theo phân loại
ban đầu. Mỗi dòng được tính một lần. Một dòng liên quan đến cặp cần xem xét
được ưu tiên xếp vào nhóm cần xem xét. Biểu đồ không đổi theo quyết định duyệt.

Thẻ Cặp đang chờ xem xét và danh sách Cặp cần xem xét đếm số cặp còn chờ hiện
tại. Số cặp giảm khi người dùng xác nhận hoặc chuyển cặp sang Chờ gộp.

Ví dụ, 500 cặp không dùng chung dòng tương ứng với 1.000 dòng thông tin.
Hai cặp A–B và B–C chỉ có ba dòng khác nhau. Không nhân hoặc chia số liệu
cho hai để làm hai chỉ số bằng nhau.

## Tên thao tác

Giữ Xác nhận gộp và Xác nhận không gộp theo hành vi hiện tại. Không đổi
Xác nhận không gộp thành Khác người, vì nhánh feature6 phân biệt hai quyết định
này bằng quy tắc xử lý riêng.

Đưa sang Chờ gộp chưa tạo file kết quả. Xem trước file kết quả (CSV) kiểm tra
các nhóm dự kiến. Xác nhận gộp và xuất CSV mới thực hiện bước xuất.

Số mục trong Chờ gộp được gọi là số đề xuất vì danh sách có thể gồm cả cặp vừa
được chọn và nhóm đã được tạo. Không gọi số đó là số hồ sơ cuối cùng.

Khi hiển thị trạng thái, ghi rõ đang chờ, dự kiến hoặc đã hoàn tất. Giữ nguyên
mã API và trạng thái lưu trong cơ sở dữ liệu; chỉ đổi cách diễn đạt trên giao diện.
