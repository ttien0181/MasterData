# Duyệt nhóm và chọn Golden record

Thay đổi ngày 2026-10-11. Thiết kế tham khảo cơ chế chọn giá trị theo từng thuộc tính và giữ nguồn gốc của [Reltio Survivorship](https://docs.reltio.com/en/objectives/resolve-potential-matches/potential-matching-at-a-glance/potential-matching-navigation/design-survivorship-rules/survivorship-rules).

## Bố cục và thao tác

- Tổng quan: tổng nhóm, bản ghi trong nhóm, nhóm nhiều thành viên, Golden chờ xác nhận; phân biệt số Golden đã xác nhận và số nhóm có trường cần xử lý.
- Biểu đồ tính trên toàn bộ phiên bản nhóm: phân bố kích thước, độ đầy đủ của sáu trường chính và mức giảm số bản ghi khi hợp nhất. Nhóm dự kiến luôn được ghi rõ; số giảm là dự kiến, không phải kết quả đã xuất bản.
- Danh sách dùng tên đối tượng và nguồn thay cho chuỗi hash. Phân trang đúng 10 nhóm, có trang trước/sau; nhóm đầu trang được mở tự động.
- Bảng so sánh: hàng là trường dữ liệu, cột Golden và các bản ghi nguồn. Bấm giá trị để chọn, nhận biết giá trị khác nhau, lọc trường, xem trường bổ sung. Trường chính nhập văn bản bình thường; trường JSON bổ sung có hướng dẫn riêng.
- Giữ lựa chọn chưa lưu khi đổi nhóm hoặc chuyển tab. Xác nhận và tự chọn lại bị vô hiệu hóa khi có lựa chọn chưa lưu.

## Tự chọn và nguồn gốc

Sử dụng bộ survivorship có sẵn trên backend: giá trị đã xác minh, chất lượng, độ ưu tiên nguồn, ngày cập nhật nguồn và thứ tự ổn định khi hòa. Không dùng thời điểm import làm thời điểm cập nhật nguồn. Giữ tất cả lựa chọn nguồn hợp lệ để đối chiếu. Bỏ qua giá trị thiếu, placeholder hoặc không hợp lệ theo metadata chất lượng.

Các phần địa chỉ chọn từ cùng bản ghi, cả khi chỉ có các phần địa chỉ mà thiếu chuỗi địa chỉ đầy đủ. Khi người dùng chọn một phần địa chỉ từ bảng, các phần liên kết đi theo nguồn đó; phần không có giá trị được để trống.

Golden được tự đề xuất khi mở nhóm và tự tạo draft khi xuất bản cluster. Nút tự chọn lại giữ lựa chọn thủ công đã lưu. Lưu lựa chọn luôn qua backend, kiểm tra ownership, capability, CSRF, revision và membership, ghi audit và hỗ trợ replay request.

Nhóm dự kiến có thể lưu dự thảo trong các bảng Golden hiện có, không cần migration. Đọc nhóm hoặc thống kê không tạo draft. Dự thảo không xuất bản membership, không xác nhận hoặc xuất Golden khi nhóm còn xung đột. Lựa chọn thủ công phù hợp thành viên được chuyển sang Golden khi tạo phiên bản nhóm đã xuất bản; lựa chọn mâu thuẫn vẫn yêu cầu duyệt.

## Chuyển tab

Bộ nhớ đệm nằm trong layout của một phiên, không lưu dữ liệu cá nhân vào localStorage. Dùng chung request đang chạy, giữ dữ liệu trang và lựa chọn bộ lọc; đổi phiên hoặc rời layout sẽ bỏ bộ nhớ đệm. Cặp cập nhật theo decision revision, nhóm theo cluster/decision revision, Golden và thống kê theo golden revision. Có nút Cập nhật để đọc lại chủ động. Request lỗi không được giữ như kết quả thành công.

## Phiên hiện tại

Đọc kiểm chứng phiên `21c9a723-8aa0-44f5-9083-dd4edc99c6ef` không đổi quyết định hoặc tạo draft: 108 nhóm dự kiến chứa đủ 1.651 bản ghi, 18 nhóm một thành viên, 90 nhóm nhiều thành viên, lớn nhất 20 thành viên. Trang đầu có 10 nhóm; mở nhóm trả cả giá trị nguồn và đề xuất Golden.

Bằng chứng kiểm thử và ảnh giao diện nằm ở `artifacts/session-workflow/20261011-workflow-01/cluster-ux-20261011/`.

Kiểm chứng: 72 kiểm thử backend liên quan và 97 kiểm thử frontend đạt; lint/TypeScript, Ruff và Docker build đạt. Browser đã lưu lựa chọn nguồn, chạy tự chọn lại và kiểm tra lựa chọn thủ công được giữ; phân trang 24 nhóm theo 10/10/4; lưu Golden dự thảo nhóm dự kiến rồi tải lại; từ chối xác nhận nhóm dự kiến; không tràn ngang trang trên màn hình 390px. Sau khi các tab đã tải, request dữ liệu giữ nguyên 11 trước/sau một vòng chuyển tab. Request điều hướng Next.js và polling workflow vẫn hoạt động.
