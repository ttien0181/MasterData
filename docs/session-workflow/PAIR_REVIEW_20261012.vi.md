# Kiểm toán và triển khai trang duyệt cặp · 12/10/2026

## Quy tắc người dùng đã chốt

- Deny là cannot-link bền vững: hai bản ghi không được nằm cùng nhóm, kể cả qua liên kết gián tiếp.
- Accept hàng loạt dùng quyền duyệt thủ công; giữ ràng buộc chặn và báo số cặp bị chặn.
- Hàng loạt chỉ xử lý UNDECIDED/DEFERRED. Giữ nguyên quyết định ACCEPTED, REJECTED và NON_MATCH đã có.
- Phạm vi gồm cả trang chưa xem: cặp tích chọn, toàn bộ kết quả bộ lọc, toàn phiên, Group hoặc Subgroup. Tìm trong trang chỉ hỗ trợ quan sát; không thu hẹp phạm vi hàng loạt.

## Thay đổi

Trang có bốn chỉ số toàn phiên, thanh tiến độ và các thẻ Group với số cặp thực tế. Tiến độ gồm kết luận theo quy tắc và quyết định đã duyệt; không đồng nghĩa mọi cặp đã được người dùng duyệt. Bộ lọc Subgroup hiển thị tên và số cặp từ engine; phần hướng dẫn giải thích Group/Subgroup khác với nhóm bản ghi sau clustering. B là tín hiệu chuẩn hóa, không được tạo thành Group quyết định giả. Các cặp NULL được hiển thị “Chưa phân nhóm”, kèm lý do thực tế.

Bảng có 10 cặp/trang, tên/nguồn/mã bản ghi, điểm quy tắc, bằng chứng và quyết định. Hộp đối chiếu hiển thị hai bản ghi cạnh nhau, dữ liệu gốc/chuẩn hóa và tùy chọn chỉ hiện khác biệt. Điểm quy tắc không được trình bày như xác suất.

Accept/Deny hàng loạt có lựa chọn phạm vi rõ ràng và bước xem trước số cặp áp dụng/bị chặn/giữ quyết định cũ. Hủy xem trước không ghi quyết định. Backend kiểm tra quyền, chủ phiên, trạng thái, revision và request id; UI chặn xác nhận khi revision thay đổi. Kết quả tác vụ báo số đã xử lý, áp dụng, bị chặn và bỏ qua. API cũ `no_merge` giữ nghĩa REJECTED; lệnh `deny` mới tạo NON_MATCH và negative edge. Không chuyển đổi lịch sử từ chối thành cannot-link.

Bulk thực thi theo lô 100 cặp, checkpoint sau commit, có thể tiếp tục sau lỗi mà không áp dụng lại các lô đã ghi. Tiến độ queue giữ các bộ đếm nghiệp vụ. Cặp INSUFFICIENT_EVIDENCE đã Accept được đưa vào kiểm tra clustering thay vì bị bỏ qua âm thầm. Các guard của engine vẫn quyết định nhóm cuối cùng.

Giữ TanStack Query và trạng thái giao diện theo phiên của cả bốn tab: bộ lọc, trang, lựa chọn, chi tiết và vị trí cuộn. Khi kết quả thu hẹp dưới trang đang xem, UI quay về trang hợp lệ. Khi revision đổi, giữ thống kê gần nhất cùng thông báo cập nhật để tránh mất các thẻ và nhảy bố cục. Label của các bộ lọc có tên truy cập rõ ràng. Thanh tiến độ dùng tiếng Việt và số định dạng thay cho tên tác vụ/các khóa kỹ thuật.

Nếu xem trước bị 409 do revision cũ ngay sau bulk, UI đọc lại workflow, giữ nguyên intent, khóa xác nhận và yêu cầu đóng/xem trước lại. Không tự gửi lại quyết định. Hai lỗi này đã được tái hiện bằng regression test thất bại trước khi sửa (`artifacts/pair-review-final-red.log`).

## Tham khảo thiết kế

Mẫu danh sách đề xuất kèm đối chiếu bản ghi và khác biệt tham khảo [Ataccama Matching Proposals](https://docs.ataccama.com/mdm/latest/matching-proposals.html). Tách lựa chọn hàng, thao tác hàng loạt và phân trang tham khảo [Carbon Data Table](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines/). Không sao chép chính sách matching, mức tin cậy hoặc quyền duyệt của các sản phẩm này.

## Triển khai và xác minh

Chỉ sử dụng `docker-compose.yml` hiện có, project `p119-session-qa`, `.env.qa`. Build production và cập nhật backend, worker, frontend; lần sửa label cuối chỉ cập nhật frontend, không ngắt tác vụ Accept người dùng đang chạy. Frontend localhost:3000 và backend localhost:8001 báo healthy. SHA-256 các module review/bulk/summary/finalize/queue trong backend đã đối chiếu với source. Không tạo Compose mới, không chạy benchmark TC khác hoặc gọi provider AI.

- Backend toàn bộ: **960 đạt, 2 bỏ qua**, 529,88 giây; log `artifacts/pair-review-backend-final.log`.
- Frontend: **98 đạt / 19 file** và TypeScript thành công; log `artifacts/pair-review-frontend-tests.log`.
- Playwright hồi quy cache/bốn tab/workflow: **9 đạt** trên frontend đang chạy; log `artifacts/pair-review-e2e.log`.
- Review độc lập read-only: không còn lỗi Important/Critical; hai minor về offset và thống kê REJECTED đã sửa và có regression test.

Kiểm tra live dùng đúng TC4 đang có, 10.000 bản ghi và 79.364 cặp. Xác minh trang đầu/tiếp/cuối, trang cuối 4 cặp, dữ liệu nguồn, đối chiếu, Group A 34 cặp và A1 31 cặp, theme tối và viewport 390 px không tràn ngang toàn trang. Trong lúc kiểm tra, người dùng đã khởi chạy Accept cho 78.912 cặp Chưa phân nhóm; lượt xác minh chỉ đọc phiên này và kiểm tra nút ghi bị khóa khi tác vụ đang chạy. Revision có thể thay đổi do tác vụ người dùng, không dùng giá trị cố định để kết luận dữ liệu không đổi.

Kiểm tra ghi live dùng phiên QA riêng với **7 bản ghi giả lập và 21 baseline cặp giả lập**, không phải kết quả scoring của benchmark. API/auth/revision/preview và bulk executor là bản đã triển khai. Khi worker duy nhất bận tác vụ người dùng, helper chỉ chạy executor trên tác vụ QA chưa được claim; không đổi cấu hình worker hoặc tạo container. Token phiên kiểm tra chỉ nằm trong bộ nhớ/process environment và được thu hồi ở cuối; không đổi mật khẩu tài khoản người dùng. Các ảnh và JSON bằng chứng nằm trong `artifacts/pair-review-20261012/live`.

Kết quả ghi QA: selected Accept 2; Group C Deny 6; Subgroup A3 Accept 4; toàn phiên Accept 5 (giữ 14, chặn 2); toàn phiên Deny 2. Kết quả cuối 12 ACCEPTED, 8 NON_MATCH, 1 REJECTED được giữ nguyên, 8 negative edge bền vững. Hai lần preview gặp revision cũ đã đọc lại workflow và chỉ tiếp tục sau thao tác xem trước/xác nhận rõ ràng. Không có JavaScript error; `originalWrites` là mảng rỗng. Xem `browser.json` và `database.json`.

Không suy diễn rằng fixture này chứng minh toàn bộ pipeline PREPARE/DECIDE/CLUSTER trên PostgreSQL; ràng buộc cannot-link qua đường gián tiếp và guard clustering được kiểm thử riêng trong backend.

Lượt E2E và ảnh cuối dùng Node 24.12.0 tạm trong `artifacts/pair-review-node`, archive đối chiếu SHA-256 chính thức. Node 24.21.0 cài trên Windows phát sinh lỗi khởi động 0xC0000005 ở lượt chụp bổ sung. Không thay bản Node toàn máy hoặc cấu hình Docker; production frontend và worker vẫn chạy. Ảnh `deployed-final.png` xác minh frontend sau lần đổi thông báo tiến độ cuối.
