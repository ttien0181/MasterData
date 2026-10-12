# Cache và trạng thái xem theo Group — 12/10/2026

Đã triển khai theo đề xuất được duyệt: quay lại Group đã đọc giữ trang, Subgroup, bộ lọc quyết định, tìm kiếm, vị trí cuộn và trạng thái mở bằng chứng. Ví dụ A trang 3 → D → A trở lại trang 3 từ cache, không thêm GET cho trang còn mới.

## Thay đổi

- `usePairReviewCache.ts`: khóa ổn định theo user/run/filter/offset/limit 10; bỏ revision toàn phiên và cursor khỏi danh tính trang. Bộ lọc rỗng được chuẩn hóa. API vẫn phân trang trên server, không tải toàn bộ cặp.
- Trang lưu revision đã quan sát khi bắt đầu đọc. Revision workflow thay đổi đánh dấu các trang cặp cũ cần cập nhật, không xóa dữ liệu. Vì API chưa cung cấp đầy đủ các phạm vi chịu ảnh hưởng của negative link, invalidation bảo thủ cho toàn bộ trang cặp trong phiên; chỉ trang được quan sát tải lại, trang không hoạt động chờ được mở.
- Thống kê dùng khóa ổn định và cập nhật nền. During BULK_DECISIONS, trang/thống kê cập nhật không thường hơn 5 giây sau lần đọc hoàn thành. Hoàn thành tác vụ bỏ giới hạn chờ bulk; request cũ trả về vẫn bị revision fence và được đọc lại với revision mới.
- Trang cặp và thống kê coi dữ liệu mới trong 120 giây; dùng gcTime 30 phút của provider hiện có. Cache/state chỉ ở bộ nhớ, tách theo người dùng/phiên, bị thu hồi khi mất quyền hoặc logout.
- Sau đọc chính, hàng đợi tuần tự tải trước tối đa một trang kế và trang đầu của ba Group khác có dữ liệu. Không chạy trong active operation; không đọc lại trang đã cache chỉ vì revision thay đổi. Hover/focus có thể tải trước Group định mở. Khi tải trước lỗi, dừng chuỗi; sử dụng fetchQuery để giữ lỗi thay vì prefetchQuery nuốt lỗi.
- `usePairGroupView.ts`: lưu context riêng cho từng Group; các slot hiện hành vẫn tương thích khi chuyển bốn URL tab của phiên. Đổi Group/filter vẫn bỏ tích chọn, đóng hộp đối chiếu; không âm thầm mở rộng scope hàng loạt.
- `useTabView.ts`: tránh cleanup effect lưu DOM đã được React tái sử dụng cho Group mới vào trạng thái Group cũ.
- Nút ghi khóa khi trang placeholder, lỗi hoặc revision cũ. Backend vẫn kiểm tra quyền, workflow/pair revision và cannot-link. Phản hồi quyết định server cập nhật dòng đã cache và làm mới danh sách/thống kê, không tự retry mutation.
- Group chưa cache có khung chờ 10 dòng; không dùng dữ liệu Group khác. Cache đang cập nhật giữ bảng và chỉ báo nhỏ. Có nút Cập nhật dữ liệu. Giữ 10 cặp/trang; pending total giảm chỉ clamp trang sau khi dữ liệu thực đã cập nhật.

## Quyết định triển khai

- Giữ nguyên logic Deny cannot-link, bulk Accept thủ công bỏ qua cặp bị chặn, và chỉ xử lý chưa duyệt/hoãn. Không sửa backend trong lượt này.
- Không đổi source data hoặc chạy benchmark khác; browser fixture dùng dữ liệu giả lập nhỏ và HTTP được chặn bằng Playwright. Không ghi vào phiên TC4 của người dùng.
- Giữ checkout feature hiện có vì chứa nhiều công việc đang triển khai; không reset, tạo commit hoặc chuyển worktree. Không tạo hoặc sửa Compose mới, chỉ dùng docker-compose.yml.
- Chưa thêm version từng Group/luồng delta backend vì phạm vi ảnh hưởng chưa đầy đủ. Backend scope/snapshot/cannot-link không được suy diễn là đã audit lại bởi lượt review frontend này.

## Xác minh

- Regression RED trước sửa: `artifacts/pair-group-cache-red.log`, `pair-group-view-red.log`, `pair-group-prefetch-red.log`, `pair-group-cache-races-red.log`.
- Frontend toàn bộ: **111/111 test**, **20 file**, 19,06 giây. `artifacts/pair-group-cache-frontend-tests.log`.
- TypeScript: `node node_modules/typescript/bin/tsc --noEmit` exit 0 với portable Node đã được kiểm tra từ lượt trước.
- Playwright trên Docker production frontend: **12/12 test**, 13,8 giây. Ba ca mới: quay lại Group/cache/scroll/Subgroup/search; Group mới trả chậm không lẫn dòng; Deny giữ bảng và khóa ghi đến khi đọc đúng revision. Các ca hồi quy bốn tab/auth/workflow/theme/mobile vẫn đạt. `artifacts/pair-group-cache-e2e.log`.
- Race bổ sung: revision tăng khi request cũ còn chạy; bulk hoàn thành khi refresh cũ chưa trả; queued prefetch gặp 401/403 không bắt đầu Group tiếp theo; dữ liệu và quyền hiện hành quyết định khả năng ghi.
- Review độc lập read-only: 28/28 ca được reviewer chạy và không có lỗi material được xác nhận. Khuyến nghị race của reviewer đã bổ sung; phát hiện lỗi tiếp tục một request sau 401/403 được sửa với RED→GREEN, toàn suite đạt. Không có minor bị hoãn.
- Production build đạt; chỉ recreate frontend bằng `docker compose --env-file .env.qa -p p119-session-qa -f docker-compose.yml up -d --no-deps frontend`. Frontend `running healthy`, HTTP localhost:3000/login trả 200; backend/database vẫn healthy, worker vẫn chạy.
- Frontend image cuối: `sha256:dd6f74207368580f71a4717f2b2908400f95eebe09be116c14965f18bd86d0f7`. Build log: `artifacts/pair-group-cache-docker-build.log`.

Trình duyệt đang mở bundle cũ cần tải lại một lần để nhận bản triển khai mới. Cache là bộ nhớ trong tab trình duyệt, không giữ qua F5/đóng tab; Group chưa từng đọc hoặc cache đã hết hạn vẫn cần request đầu tiên.
