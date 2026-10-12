# Cache và trạng thái bốn tab của phiên — 12/10/2026

Đã triển khai TanStack Query 5.104.2 và kho trạng thái giao diện dùng chung trong lớp đăng nhập `AppSession`. Bốn tab Dữ liệu, Phân loại, Nhóm và Golden sử dụng cùng cache. Frontend mới đã được build production và triển khai tại `http://localhost:3000` bằng service `frontend` trong `docker-compose.yml` hiện có, project `p119-session-qa`, cấu hình `.env.qa`.

## Hành vi

- Quay lại trang đã đọc hiển thị dữ liệu từ bộ nhớ ngay. Trang chưa đọc vẫn cần request đầu tiên.
- Giữ phân trang, chế độ gốc/chuẩn hóa, bộ lọc, tìm kiếm trong trang, lựa chọn cặp/nhóm, nhóm đang mở, phân trang thành viên/bằng chứng, chi tiết đã mở, cuộn dọc/ngang và lý do mở lại phiên.
- Golden giữ bản nháp cùng revision gốc khi chuyển tab. Nếu server đã đổi revision, bản nháp vẫn còn để đối chiếu nhưng nút lưu bị chặn. Chọn nhóm khác, kể cả qua URL, yêu cầu xác nhận nếu phải bỏ bản nháp hiện tại.
- Lưu hoặc thao tác workflow đọc lại metadata từ server. Revision mới tạo cache identity mới cho tài nguyên liên quan; dữ liệu đầu vào đã PREPARE không bị xóa theo mỗi thay đổi quyết định/Golden.
- Lỗi đọc trang giữ lại trang đã đọc trước đó, thông báo rõ trạng thái và cung cấp nút thử lại. Không tự động lặp lại mutation.
- Cache tách theo người dùng và phiên. Đăng xuất/đổi người dùng xóa bộ nhớ; 401 từ query hoặc mutation xóa dữ liệu và nháp rồi chuyển về đăng nhập. Mất quyền đọc/phiên không còn tồn tại xóa cache của phiên và chặn hiển thị đến khi thử lại thành công.

## Chính sách kỹ thuật

| Tài nguyên | Freshness và cập nhật |
|---|---|
| Metadata workflow | Fresh 15 giây; revalidate khi focus/reconnect nếu đã stale; một polling trung tâm mỗi 1,8 giây khi có active operation |
| Dữ liệu đã PREPARE | Fresh vô hạn trong bộ nhớ vì input đã được đóng; khi PREPARE hoàn tất chuyển sang identity `ready` |
| Dữ liệu đang PREPARE | Fresh 2 giây và gắn workflow revision |
| Cặp | Gắn decision revision, bộ lọc và cursor; fresh 60 giây |
| Danh sách nhóm | Gắn cluster/golden revision và cursor; fresh 60 giây |
| Thành viên/bằng chứng | Gắn nhóm, cluster revision và hai cursor; fresh 60 giây |
| Golden | Gắn cluster/golden revision, trạng thái phiên và cursor; fresh 60 giây |
| Resolution | Gắn workflow revision; trang thay đổi gắn operation ID và offset |

Query không còn được dùng được thu gom sau 30 phút. Trạng thái giao diện không hoạt động có thời hạn 30 phút; bản nháp Golden chưa lưu được giữ đến khi lưu, bỏ hoặc đăng xuất. Kho này chỉ nằm trong RAM, không ghi hồ sơ/bản nháp vào localStorage, sessionStorage hoặc IndexedDB. F5, đóng trình duyệt hoặc mở tab trình duyệt mới sẽ tạo bộ nhớ mới; trạng thái được giữ cho điều hướng trong ứng dụng, gồm Back/Forward.

`useTabView` khôi phục các phần chi tiết có dữ liệu tải muộn bằng MutationObserver. Nếu chiều cao trang ban đầu làm trình duyệt giới hạn vị trí cuộn, nó thử khôi phục lại khi nội dung đến, và dừng khi người dùng bắt đầu tương tác.

Chính sách theo [TanStack Query defaults](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults) và [pagination](https://tanstack.com/query/latest/docs/framework/react/guides/paginated-queries); freshness, lifetime, retry và placeholder đều được đặt rõ cho ứng dụng này.

## Kiểm chứng

- Vitest toàn bộ frontend: **90 passed / 18 files**. Bao gồm cache giữa tab, cách ly user/run, đổi decision revision, Golden draft/revision, lỗi trang và retry, 401 mutation, mất quyền đọc, tab từ chưa sẵn sàng sang sẵn sàng, cuộn với nội dung tải muộn.
- TypeScript `npm run lint`: passed.
- Playwright trên frontend production đang chạy: **9 passed**. Ba kịch bản cache mới và sáu kịch bản workflow hiện có; API được mô phỏng để kiểm tra UI mà không chạy pipeline hoặc nhà cung cấp AI. Kịch bản bốn tab kiểm tra điều hướng URL/Back/Forward, tất cả phân trang liên quan, lựa chọn, nháp, chi tiết, cuộn và lưu Golden. Danh sách GET trước/sau lượt quay lại hoàn toàn bằng nhau; sau lưu, Golden hiển thị giá trị của revision mới và Data chỉ có hai GET cho hai trang ban đầu.
- Trình duyệt với API thật: tái sử dụng **TC4, 10.000 bản ghi**, run `9a1dba38-4491-448e-9fb2-6c3507efdecb`. Duyệt trang đầu, trang 2, cuối và trước cuối chỉ phát sinh bốn GET Data tương ứng. Quay lại trang đã đọc và vòng qua ba tab khác không phát sinh thêm GET Data; giữ trang 2, chuẩn hóa và chi tiết đang mở. Không có JavaScript page error; giao diện sáng/tối và chiều rộng 390 px không tràn trang.
- Phiên benchmark thật đang `DATA_READY`, nên ba tab xử lý hiển thị trạng thái chưa sẵn sàng. Hành vi phân loại/nhóm/Golden khi đã sẵn sàng và lưu Golden được kiểm tra bằng fixture trình duyệt và integration tests, không gọi lại engine/AI trên benchmark.
- Code review lần hai không còn phát hiện quan trọng; hai tình huống cuộn muộn được xử lý thêm và có kiểm thử.
- Next.js được vá từ 16.3.6 lên **16.3.8**, source-map-js từ 1.2.1 lên **1.2.2**, xử lý hai cảnh báo có sẵn. `npm audit --json`: **0 vulnerabilities**.
- Docker build passed; container frontend healthy, `NODE_ENV=production`, Next 16.3.8 và dependency TanStack 5.104.2. Backend/worker/database hiện có tiếp tục chạy. Cấu hình backend của stack QA vẫn là môi trường kiểm thử; bước này không chứng nhận hạ tầng public production.

## Bằng chứng và mã nguồn

- `artifacts/session-cache-frontend-tests.log`, `session-cache-tsc.log`, `session-cache-e2e.log`.
- `artifacts/session-cache-build.log`, `session-cache-deploy.log`, `session-cache-npm-audit-final.json`.
- `artifacts/session-cache-20261012/live/browser.json`, `desktop.png`, `dark.png`, `mobile.png`.
- `frontend/src/components/SessionCache.tsx`, `useSessionRead.ts`, `useTabView.ts`, `SessionWorkspace.tsx` và bốn màn hình hiện có.
- `frontend/src/components/SessionCache.integration.test.tsx`, `useTabView.test.tsx`, `frontend/e2e/session-cache.spec.ts`.

Không tạo Compose mới, không đổi thuật toán backend trong tác vụ cache, không tải thêm benchmark TC, không commit/push.
