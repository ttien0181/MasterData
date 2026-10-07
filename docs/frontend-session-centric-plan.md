# Kế hoạch chuyển frontend MasterOne sang workspace theo phiên

Ng?y ??i chi?u ban ??u: 07/10/2026. C?p nh?t hi?n t?i: theo spec v3, G?0?3 ?? tri?n khai; CP08 ?? ki?m ch?ng t?ng h?p ba CSV v? snapshot qua R2; CP09 c? review ba c?t; CP10 ?? c? preview/export/history, audit API/UI v? compare snapshot R2. CP11 ???c x?c minh m?t ph?n: frontend v? backend tr?ng t?m ??t, R2 live smoke ??t; browser E2E, replay d? li?u chu?n v? benchmark nhi?u tri?u d?ng c?n thi?u. Retention/purge, multi-run series v? export n?n v?n l? backlog. C?c s? CP01?CP04 trong ph?n c? thu?c c?ch chia frontend tr??c ??, kh?ng tr?ng v?i G? trong spec v3.

## 1. Mục tiêu và căn cứ

Triển khai đầy đủ cả light mode và dark mode theo yêu cầu bổ sung của người dùng. Áp dụng top bar có phiên hiện tại, dock góc nhìn, drawer nạp dữ liệu, Sessions Hub và review ba cột. Điều chỉnh hành vi theo hệ thống thực tế để giữ đúng dữ liệu, quyết định review và CSV xuất. Hai theme là tiêu chí nghiệm thu ngang nhau của bản đầu.

Ba tài liệu người dùng cung cấp được dùng làm tài liệu tham khảo thiết kế:

- `D:/Downloads/masterone_login_light.html`: trang đăng nhập hai cột và nội dung thương hiệu.
- `D:/Downloads/Mo - linh vật MasterOne.html`: hình dáng, màu và ý nghĩa linh vật Mo.
- `D:/Downloads/MasterOne - giao diện light mode.html`: bố cục, màu sắc, Workspace, Hub, Review.
- `D:/Downloads/MasterOne - Đổi file và file lỗi.html`: drawer, đổi file, lỗi và bản nháp.
- `D:/Downloads/MasterOne_UI_UX_Session_Centric_Concept_v2 (1).md`: định hướng trải nghiệm.

Các câu “quyết định đã chốt” trong concept là nội dung của tài liệu, không tự động trở thành yêu cầu thay đổi backend, chính sách lưu trữ hoặc quyền truy cập. Các con số trong mockup là minh họa, không đưa vào UI chạy thật.

Căn cứ code chính:

| Phạm vi | File hiện có |
|---|---|
| Shell, điều hướng | `frontend/src/components/AppShell.tsx` |
| Phiên, upload, polling, tổng quan | `frontend/src/components/ResolutionWorkspace.tsx` |
| Review, chờ gộp, xem trước, xuất | `frontend/src/components/ResolutionFlow.tsx` |
| Lựa chọn nguồn cho trường | `frontend/src/lib/resolutionChoices.ts`, `resolutionFields.ts` |
| Hợp đồng API | `frontend/src/lib/api.ts`, `contracts.ts` |
| Đọc/tạo/chạy run | `backend/src/masterone/api/mdm_runs.py` |
| Review, export, snapshot, tách bản ghi | `backend/src/masterone/api/mdm_resolution.py` |
| Parse CSV và giới hạn | `backend/src/masterone/mdm/resolution_import.py` |
| Trạng thái và worker | `backend/src/masterone/mdm/resolution_service.py`, `resolution_worker.py` |
| Auth và nền tảng RBAC | `backend/src/masterone/api/auth.py`, `auth/security.py` |
| CSS/theme | `frontend/app/layout.tsx`, `globals.css`, `dashboard.css`, `glass.css` |

## 2. Những chỗ cần chỉnh so với bản thiết kế

| Thiết kế tham khảo | Thực tế repo | Cách áp dụng |
|---|---|---|
| Analysis Session | Đã có `ResolutionRun`, cô lập theo người sở hữu | Một phiên phân tích = một run; giữ `run_id`, không tạo mô hình song song |
| Tạo phiên khi bấm xử lý | `POST /mdm/runs` đã tạo run sau khi kiểm tra và lưu upload; `/start` là bước riêng | Gọi phiên `UPLOADED` là “Đã nạp, chưa xử lý”; sửa nội dung “chưa có phiên” trong mockup |
| Một file và lựa chọn nguồn CRM/ERP/Billing | Luồng chính hỗ trợ nhiều CSV; nguồn lấy từ từng dòng | Giữ danh sách nhiều file; CRM/ERP/BILL là dữ liệu nguồn, không phải các connector đã kết nối |
| File 148 MB, hơn 100.000 dòng | Trước CP2, luồng run giới hạn 5 MiB và 10.000 dòng/file | CP2 đã bỏ trần byte và số dòng ở tầng ứng dụng; giữ tối đa 10 file/phiên. Dung lượng thực tế tùy hạ tầng, chưa benchmark nhiều triệu dòng |
| Đã có hỗ trợ file lớn | Có mã R2/large-imports riêng nhưng chưa nối vào `ResolutionWorkspace` | Tích hợp large-file thành hạng mục riêng; lưu file thành công không đồng nghĩa đã phân giải dữ liệu |
| Tên phiên tùy chỉnh | Snapshot trước CP2 chưa có trường tên và API đổi tên | CP2 đã thêm persisted name và API rename; thao tác được ghi audit |
| Golden Record, Approve/Reject | Luồng thật gồm Review → Chờ gộp → chọn trường → preview → execute/export | Dock giữ “Chờ gộp”; cột phải ghi “Bản ghi hợp nhất đề xuất”; nút ghi “Đưa sang Chờ gộp” và “Không gộp” |
| Hai bản ghi CRM/ERP | Một item có thể có nhiều bản ghi, kể cả cùng nguồn | Bảng so sánh phải hỗ trợ N bản ghi và ID nguồn, không hardcode hai cột |
| Confidence 91%, AI quyết định | Score là điểm rule; Gemini hỗ trợ giải thích, không quyết định gộp | Dùng “Điểm đối chiếu” nếu cần; không mô tả là xác suất trùng hay độ chắc chắn của AI |
| Matching 72%, timeline đầy đủ | Callback hiện ở mốc `blocking` và `persisting`; kết quả được lưu theo giao dịch | Dùng trạng thái không xác định phần trăm; không suy diễn tiến độ matching từ số bản ghi |
| COMPLETED = hoàn tất mọi công việc | Pipeline hoàn tất vẫn có thể còn review/chưa xuất CSV | Tách trạng thái xử lý và khối lượng công việc còn lại |
| Dashboard chất lượng, xung đột, thực thể | Overview chưa cung cấp đủ các aggregate này | Chỉ hiện metric có nguồn và đơn vị rõ ràng; chart bổ sung cần API riêng |
| Hub đủ tên, nguồn, KPI, bộ lọc | Snapshot trước CP2 có phân trang offset, thiếu query/archive/bulk | CP2 đã thêm cursor pagination, search/filter, quick view và bulk archive/restore; chỉ lấy run của owner |
| Trạng thái toàn cục | Frontend hiện chỉ poll run đang chọn mỗi khoảng 900 ms | Bổ sung endpoint summary phạm vi tài khoản; không cộng 25 run đầu rồi gọi đó là tổng toàn cục |
| Steward/Viewer | Backend có model/helper RBAC; `/me` không trả role, mutation của run chủ yếu kiểm tra auth/CSRF/owner | Bổ sung capability contract và kiểm tra quyền tại endpoint trước khi đưa quyền này lên UI |
| Chia sẻ link cho đồng nghiệp | Run đang thuộc tài khoản tạo | Deep link không cấp quyền; tài khoản khác vẫn bị từ chối nếu chưa có mô hình chia sẻ |
| Audit toàn phiên | Bảng event đã có nhưng thiếu route đọc cho run | Đã có API/UI chỉ đọc, phân trang/lọc theo action/cặp/đề xuất; chỉ tài khoản sở hữu run, mọi role trừ Viewer |
| Export nền, nhiều phiên, XLSX/JSON | Luồng chính preview/execute trả CSV; có snapshot và tải lại | Bản đầu giữ CSV một phiên, preview, cảnh báo và idempotency; export nền là dự án bổ sung |
| Lưu vĩnh viễn, phân tầng lạnh, Series | Chưa có đầy đủ schema/service tương ứng | Đưa vào backlog sản phẩm/hạ tầng; UI không cam kết đã hỗ trợ |

Lưu ý phát hiện từ code: endpoint so sánh snapshot hiện đọc trực tiếp `snapshot.csv_bytes`, trong khi export có thể chỉ lưu `r2_key`. Cần kiểm tra và sửa đường đọc dữ liệu này trước khi nghiệm thu lịch sử trên cấu hình R2.

## 3. Phạm vi giao diện bản đầu

Dock sau xử lý: **Tổng quan · Cần review · Chờ gộp · Lịch sử xuất · Quá trình · Audit**. Audit đã nối API; “Chất lượng”, Series, so sánh nhiều phiên và export nền/nhiều định dạng chưa nằm trong bản đầu.

Top bar: MasterOne, tên hiển thị phiên, trạng thái xử lý, chuyển phiên nhanh, tài khoản và nút chuyển light/dark. Giữ đăng nhập/đăng xuất và lựa chọn theme đã lưu; theo theme hệ điều hành khi người dùng chưa chọn. Light và dark cùng bố cục/chức năng, có bảng màu riêng phù hợp độ tương phản. Không dựng chuông thông báo hoặc ô tìm kiếm toàn hệ thống khi chưa có chức năng thật.

Hai thao tác cạnh workspace: nạp dữ liệu và mở Phiên phân tích. Tablet/mobile chuyển về vị trí trong header để tránh che nội dung. Dock và status bar có vùng đệm riêng, không đè lên nút xác nhận hay hàng cuối của bảng.

Review: trái là hàng đợi có phân trang/lọc; giữa là dữ liệu nguồn và bằng chứng; phải là giá trị đề xuất cùng nguồn đã chọn. Màn hình hẹp chuyển thành danh sách → chi tiết hoặc các tab trong chi tiết; không ép ba cột vào mobile.

Đề xuất dùng URL `/app?run=<id>&view=review&item=<id>` cho bản đầu, kèm tham số bộ lọc/phân trang và `panel=sessions` khi cần. Đây là phương án ít xáo trộn route `/app`; vẫn hỗ trợ refresh, Back/Forward và gửi link. Không lưu riêng một bản runId trong URL và một bản state không đồng bộ.

## 4. Các checkpoint triển khai

Mỗi checkpoint có thay đổi cụ thể, kiểm tra và điều kiện nghiệm thu. Chỉ chuyển bước khi các tiêu chí bắt buộc của bước đã đạt; lỗi có sẵn được ghi riêng để không nhầm là lỗi do UI mới.

### CP00 — Chốt hiện trạng và dữ liệu đối chứng

- Ghi commit, môi trường, cấu hình có ảnh hưởng hành vi; không ghi bí mật.
- Chạy test frontend/backend và build; lưu log baseline.
- Chụp luồng cũ bằng một tài khoản test, một run xuyên suốt: upload → review → chờ gộp → preview → export → lịch sử/tách.
- Dùng fixture nhỏ cho kiểm thử tự động; dùng ba file `masterone_test/{crm,erp,bill}.csv` cho kiểm tra tích hợp 21.000 dòng.
- Lưu số lượng API, lựa chọn review, nguồn của từng trường, file xuất để đối chiếu sau thay UI. So sánh nội dung ngữ nghĩa thay vì hash toàn file khi ID hoặc thời gian thay đổi.

**Đạt khi:** có baseline lặp lại được, phân biệt lỗi cũ/mới, có đường khởi động môi trường test riêng. Báo cáo `eval/results/report.md` là tài liệu tham khảo; chính báo cáo ghi ảnh và CSV không cùng run, nên chưa thay thế được baseline liên tục này.

### CP01 — Chốt hợp đồng phiên, trạng thái, quyền và số liệu

**Đã triển khai phần nền CP01 ngày 06/10/2026:** xem [hợp đồng và ánh xạ vào kiến trúc hiện tại](architecture/session-contract-cp01.md) và [kết quả kiểm tra](cp01-validation.md). Tên phiên, Hub summary/filter và audit read được đặc tả cho các checkpoint tiếp theo; không dựng endpoint giả. Cổng kiểm tra toàn repo còn lỗi nền được ghi rõ trong báo cáo.

- Tạo adapter `Run → SessionViewModel`, không đổi tên bảng/ID hàng loạt.
- Phân biệt trạng thái upload ở trình duyệt, trạng thái task ở server và trạng thái công việc review.
- Xác định metric: bản ghi, cặp ứng viên, item review, cụm chờ gộp, dòng xuất. Không cộng các đơn vị khác nhau thành một tổng “trùng”.
- Ghi schema API bổ sung tối thiểu: tên phiên nếu triển khai, summary/filter danh sách, summary toàn cục, task mới nhất/error, audit read và capabilities.
- Mở rộng auth response để có quyền sử dụng thật; áp capability trên các endpoint upload/start/review/preview/execute/split/download theo ma trận đã định. Quyền trên bản ghi vẫn kiểm tra ownership.
- Không suy ra các role là chuỗi thừa kế hợp lý cho mọi thao tác chỉ từ `_ROLE_RANK`; lập capability matrix cho VIEWER, STEWARD và các role có sẵn.
- Xử lý tài khoản cũ một cách tường minh: default model hiện là VIEWER nhưng fixture và luồng cũ vẫn thao tác như steward. Không tự nâng toàn bộ tài khoản; seed tài khoản test đúng role, ghi phương án chuyển đổi tài khoản triển khai.

**Kiểm thử:** API contract, ownership hai tài khoản, Viewer gọi trực tiếp mutation bị 403, CSRF vẫn hoạt động, deep link trái quyền không lộ dữ liệu, migration đọc được run cũ.

**Đạt khi:** UI biết hành động nào được phép và mọi KPI có nguồn/đơn vị; quyền được bảo vệ ở server. API mới là thay đổi tương thích ngược hoặc có kế hoạch chuyển đổi rõ ràng.

### CP02 — Token và shell cho cả light mode / dark mode

- Tách màu nền, surface, border, chữ, cyan chủ đạo, màu trạng thái, spacing, radius và shadow thành CSS token.
- Hiện tại `glass.css` chứa hai bộ token light/dark và được import sau `dashboard.css`, nhưng `dashboard.css` vẫn khai báo token nền; các stylesheet còn có màu trực tiếp. Màu chưa tập trung hoàn toàn vào một file.
- Khi triển khai, gom định nghĩa màu theme vào một file đề xuất `frontend/app/themes.css`: `:root, html[data-theme="light"]` và `html[data-theme="dark"]`. File này chưa được tạo trong bước lập kế hoạch. Chuyển các định nghĩa trùng khỏi stylesheet khác; kiểm tra thứ tự import để không còn override màu ngoài ý muốn.
- Component dùng token theo vai trò như nền, chữ chính/phụ, đường viền, primary, success, warning, danger, nguồn dữ liệu và biểu đồ; mỗi vai trò có giá trị riêng cho hai theme. Bao gồm hover/active/selected/disabled/focus, tooltip, modal, backdrop và gradient. CSS bố cục vẫn ở file component, không gom toàn bộ CSS vào file theme.
- Kế thừa `data-theme`, khóa `masterone.theme` và script khởi tạo trước khi render; chuyển theme không reload, không mất phiên/filter/draft, không lóe sai nền khi tải trang. Rà soát màu trực tiếp ở landing/auth và component dùng chung để chúng cũng hiển thị đúng trong hai chế độ.
- Chuyển top bar/dock/status bar sang component riêng; giới hạn CSS mới dưới root workspace để không làm hỏng landing/login/register.
- Dùng CSS Grid/Flex responsive; giữ CSS cũ cho chế độ legacy trong thời gian chuyển.
- Dùng font hiện có ở đợt đầu; đổi sang Be Vietnam Pro là thay đổi có chủ đích sau khi kiểm tra tiếng Việt, tải font và layout.
- Thiết lập cờ cấu hình chuyển giao diện cũ/mới để có đường quay lại khi kiểm thử.

**Kiểm thử:** screenshot cùng dữ liệu ở cả light và dark tại 1440×900, 1280×720, 768×1024, 390×844; zoom 200%; bàn phím; reduced motion; không che CTA và không cuộn ngang toàn trang. Kiểm tra chữ/biểu đồ/badge và các trạng thái loading/empty/error/disabled trong cả hai theme. Kiểm tra lần đầu theo hệ điều hành, đổi theme, refresh giữ lựa chọn, hệ điều hành đổi theme khi chưa chọn thủ công và khởi tạo không lóe nền sai.

**Đạt khi:** cả hai theme hoạt động đầy đủ, màu chủ đề có một nơi định nghĩa, component dùng token; khung mới hoạt động với nội dung hiện có và không thay đổi kết quả nghiệp vụ.

### CP03 — Điều hướng phiên và Sessions Hub

**Tình trạng:** Điều hướng URL, khôi phục phiên, phân trang server và bảo vệ race đã làm. CP03 tự nó chưa phải bản dựng UI theo HTML mẫu; lớp thị giác được triển khai tiếp ở CP04–CP09. API hiện có đáp ứng logic chọn phiên.

- Theo kiến trúc hiện có, `ResolutionWorkspace` sở hữu state phiên/góc nhìn; URL là nguồn lựa chọn chính và localStorage chỉ hỗ trợ khôi phục khi URL không chỉ định.
- Khi đăng nhập: ưu tiên URL hợp lệ → phiên đang xử lý phù hợp → phiên gần nhất → empty state. Xử lý run đã mất quyền/không tồn tại và localStorage cũ.
- Hub: list có phân trang phía server, click để xem nhanh, “Mở phiên” để chuyển workspace, đóng quay về đúng vị trí.
- Không hiển thị checkbox hàng loạt khi chưa có thao tác hàng loạt thật. Không chỉ tìm kiếm trên 25 run đầu mà mô tả là tìm toàn bộ.
- Chặn phản hồi cũ của run A ghi đè run B; xóa selection/filter/preview không còn hợp lệ khi chuyển run. Bản nháp lựa chọn có khóa theo run và người dùng.

**Kiểm thử:** A→B nhanh với response đảo thứ tự, refresh link, Back/Forward, đóng Hub, hơn 25 phiên, lỗi tải/thử lại, không có localStorage, phiên tài khoản khác.

**Đạt khi:** run trên URL/top bar/KPI/hàng đợi/export luôn là cùng một run; không mất bản nháp đã lưu.

### CP04 — Dựng lại khung Workspace và Sessions Hub theo mẫu

**Tình trạng 07/10/2026:** Đã thay sidebar cố định bằng top bar có linh vật Mo, dock góc nhìn ở đáy, nút tròn Nạp/Phiên hai bên và status bar toàn cục. Sessions Hub chuyển thành overlay toàn màn hình với danh sách/trạng thái bên trái và preview bên phải. Nội dung vẫn lấy từ API/metrics có thật; CP04 không sửa backend. TypeScript lint, 29 frontend tests và production build đều đạt; chưa chạy browser screenshot/UAT.

- Bám bố cục và nhịp thị giác trong `D:/Downloads/MasterOne - giao diện light mode.html`, nhưng không chép số minh họa hay nút không được backend hỗ trợ.
- Giữ run đang chọn làm workspace; các view đổi qua dock và deep link đã làm ở CP03.
- Hub dùng toàn màn hình, preview cạnh danh sách; mở phiên đổi workspace, đóng quay lại đúng vị trí. Không bật bulk action/compare/export/retry nếu chưa có capability và API phù hợp.
- Dùng vector Mo từ `D:/Downloads/Mo - linh vật MasterOne.html`; cùng component có thể tái sử dụng ở auth trong CP05.
- Giữ CSS layout trong stylesheet giao diện và màu theo `themes.css`. Cờ `NEXT_PUBLIC_MASTERONE_SHELL=legacy` hiện chỉ áp dụng một số kiểu phẳng legacy; không xem đây là rollback đầy đủ sau lần đổi cấu trúc CP04.

**Kiểm tra:** `npm run lint`, Vitest; xác nhận desktop/mobile, cả hai theme, dock không che nội dung, focus và overlay hoạt động. Browser screenshot/UAT vẫn cần làm ở CP11.

**Đạt khi:** khung sử dụng cùng ngôn ngữ thị giác với tài liệu mẫu, giữ được dữ liệu/quyền/luồng backend hiện tại và không đưa metric minh họa lên sản phẩm.

### CP05 — Đăng nhập và nhận diện Mo

- Dựng lại trang đăng nhập theo `D:/Downloads/masterone_login_light.html`: khung hai cột, thông điệp thương hiệu bên trái, form bên phải, trạng thái hệ thống và footer.
- Tái sử dụng vector Mo; giữ API login, lỗi xác thực và điều hướng Next hiện có. SSO/Quên mật khẩu chỉ hiển thị nếu hệ thống có luồng thật.
- Áp dụng light/dark token hiện có, responsive xuống một cột và form dùng được bằng bàn phím.

**Kiểm tra:** login đúng/sai, loading, link đăng ký, dark/light, 1440px và mobile.

**Đạt khi:** hình thức theo mẫu nhưng không hiển thị tính năng bảo mật/SSO giả.

### CP06 — Drawer upload, đổi file và lỗi

- Tách hai thao tác “Tải lên và kiểm tra” và “Xử lý dữ liệu trùng” theo hai endpoint hiện có.
- Trước khi POST: thêm/bỏ/đổi nhiều CSV ngay trong draft. Giữ tên người dùng đã chỉnh khi thay file.
- Upload hợp lệ tạo run `UPLOADED`; đóng drawer giữ trạng thái trong provider ngoài drawer. Lần mở lại có thể tiếp tục chạy run đã nạp.
- Đổi file sau khi đã có run: tạo phiên nạp mới, giữ phiên cũ; không âm thầm sửa file của run có kết quả. Bỏ câu “file cũ tự hủy” trong mẫu.
- Sau reload, file chưa upload phải được chọn lại; chỉ khôi phục metadata an toàn. Run đã upload có thể lấy lại từ backend.
- Nếu cần phần trăm byte, thêm transport XHR multipart có cookie/CSRF và callback thật; 100% byte gửi xong vẫn phải chờ backend kiểm tra trước khi bật xử lý.
- Đóng drawer không hủy request khi trang còn mở. Đóng tab không được hứa tiếp tục upload nền. Abort phía client không bảo đảm server đã hủy run; phải đối soát trước khi retry POST.
- Hiện lỗi lâu đủ để sửa: tên file, nguyên nhân, dòng nếu backend trả; lỗi hiện tại chủ yếu là chuỗi/first-error, không giả danh sách nhiều lỗi chưa được kiểm tra.
- Chống bấm đôi; với mất response của create-run, đối soát hoặc bổ sung idempotency trước khi retry tự động.

**Kiểm thử:** CSV đúng, rỗng, UTF-8 BOM, sai encoding, thiếu cột, sai số cột, `pairs.csv`, source sai, ID trùng trong/giữa file, quá giới hạn, nhiều file có một file lỗi, mất mạng, đóng/mở drawer, upload xong nhưng start lỗi.

**Đạt khi:** không chạy dữ liệu chưa hợp lệ; không mất file cũ đã nạp hoặc âm thầm tạo nhiều phiên do bấm lặp; nhãn giới hạn khớp backend.

### CP07 — Processing và status bar đúng sự thật

**Tình trạng:** Đã hoàn thiện phần theo dõi GĐ3 ngày 07/10/2026: poll tuần tự có backoff và stale state; thanh trạng thái query danh sách active theo owner, mở lại được run đang chạy. Kết quả và lệnh kiểm tra nằm trong [spec v3, mục 13.3](MasterOne_Design_Spec_NextJS_v3.md#133-kết-quả-checkpoint-gđ3-2026-10-07) và [checklist GĐ3](cp03-validation.md).

- Map `UPLOADED`, `QUEUED`, `RUNNING`, `COMPLETED`, `RETRYABLE_FAILED`, `FAILED` sang nhãn rõ ràng. Mã chưa biết có fallback, không mặc định là hoàn tất.
- Với instrumentation hiện tại, dùng indeterminate trong tính toán; ở `persisting` ghi “Đang lưu kết quả”, chưa báo toàn bộ hoàn tất dù counter đã đạt tổng.
- Retry cùng run chỉ theo điều kiện backend; run đã hoàn tất không có nút start lại giả. Muốn chạy lại dữ liệu là luồng tạo run mới riêng.
- Poll theo vòng đời, dừng/dọn khi unmount/logout, có backoff khi lỗi; mất kết nối hiển thị dữ liệu cũ kèm trạng thái chưa cập nhật, không biến thành pipeline thất bại.
- Status bar lấy summary phạm vi tài khoản, vẫn theo dõi run đang chạy khi đang xem run cũ. Không poll overview nặng cho tất cả run ở chu kỳ 900 ms.
- Nếu cần timeline có thời gian từng bước hoặc kết quả tạm thời: thêm event/telemetry backend rồi mới hiển thị; bản đầu chỉ hiển thị thông tin thật đang có.

**Kiểm thử:** queue nhiều run với một worker, restart worker → interrupted, retry, backend ngắt tạm thời, reload khi chạy, chuyển run trong lúc poll, tổng bằng 0, counter đổi phase.

**Đạt khi:** không có phần trăm giả, không báo pipeline lỗi chỉ vì mất mạng, không rò timer/request hoặc nhân đôi task.

### CP08 — Tổng quan với KPI đúng nghiệp vụ

**Tình trạng:** KPI overview và nhãn phase vẫn lấy từ API; test xác nhận giữ trạng thái overview cũ khi kết nối lỗi. Xem [kết quả GĐ3](cp03-validation.md).

- Bản đầu dùng record_count, file_count, source_counts, counts.auto, counts.review, counts.separate, history_count và candidate_count.
- Ghi rõ “cụm chờ gộp”, “item cần review”, “bản ghi dự kiến giữ riêng”, “CSV đã xuất”. Không gọi mọi item chờ gộp là đã gộp.
- Giữ biểu đồ chờ gộp/review và phân bố nguồn; click KPI/chart mở đúng view/filter.
- Chưa hiển thị “số thực thể cuối” khi chưa có preview hoặc snapshot; nếu lấy từ preview phải ghi “dự kiến”.
- Chất lượng/xung đột/phân bố score chỉ đưa vào sau khi có aggregate và định nghĩa mẫu số; không tải toàn bộ record về browser để tự tính.

**Kiểm thử:** API và UI cùng số/đơn vị; dữ liệu rỗng, tất cả giữ riêng, còn review, số lớn; filter click đúng; loading khác 0 thật.

**Đạt khi:** KPI giải thích được và dẫn đến dữ liệu tương ứng, không chứa số minh họa.

### CP09 — Review ba cột và Chờ gộp

**Đã triển khai UI ba cột:** hàng đợi, so sánh nguồn và panel giá trị/giải thích/quyết định hiện đồng thời. Giữ API, capability, lựa chọn theo `source_record_id`, lưu từng phần, chuyển review, từ chối và tách xung đột; không dùng score làm xác suất. Kết quả và checklist thủ công nằm ở [CP09 validation](cp09-validation.md). So sánh trực quan với mockup và browser E2E vẫn thuộc GĐ7.

- Tách `ResolutionFlow` theo trách nhiệm: queue, comparison, field choices, evidence và actions; giữ API nghiệp vụ hiện có.
- Hỗ trợ N records, trường mở rộng, giá trị giống nhau, missing_fields và provenance theo `source_record_id`.
- Giữ lưu nháp một phần, chuyển lựa chọn từ review sang merge, không cho xuất khi thiếu lựa chọn bắt buộc.
- Giữ các nhánh `can_merge=false`, staged conflict, giải quyết xung đột, không gộp và tách bản ghi; không tạo nút “Duyệt” vượt các chặn này.
- Hiện lời giải thích Gemini theo yêu cầu; lỗi/timeout Gemini không làm mất khả năng review bằng bằng chứng rule.
- Khi chọn case khác với sửa đổi chưa lưu, cho lưu hoặc bỏ thay đổi; sau quyết định chọn item tiếp theo hợp lệ trong filter hiện tại.

**Kiểm thử:** mở rộng bộ `workspace.test.tsx` và `resolutionFields.test.ts`; E2E dữ liệu 2/3/nhiều records, cùng nguồn, nguồn khác nhau, giá trị rỗng, lưu một phần, reject, conflict, chuyển phiên, quyền Viewer, bàn phím.

**Đạt khi:** kết quả quyết định và lựa chọn nguồn tương đương giao diện cũ; không mất lựa chọn hoặc cho gộp một nhóm đang bị chặn.

### CP10 — Preview, CSV, lịch sử và Audit

**Audit đã triển khai ở phạm vi cơ bản:** API chỉ đọc có phân trang và lọc theo action/pair/proposal; UI hiển thị actor, thời điểm, loại event và chi tiết. Steward, Senior Steward, Policy Admin và Auditor đọc được audit của run thuộc chính tài khoản; Viewer nhận 403. Các thao tác mới tạo phiên, start/retry, export và những thao tác review/Golden/rename/archive vốn đã được audit đều xuất hiện. Event trước khi audit UI/API được thêm không được tái tạo.

- Một entry point export từ workspace/Hub nhưng dùng cùng chuỗi export-readiness → preview → execute.
- Đưa người dùng tới item chặn xuất; yêu cầu xác nhận khi còn review chưa xử lý. Những bản ghi đó vẫn được giữ riêng theo backend.
- Giữ preview expiry/stale checks và `execute_request_id`; khi retry cùng thao tác không tạo hai lần thực thi.
- Lịch sử xuất gồm snapshot, tải lại, so sánh và tách bản ghi tạo snapshot con. Phân biệt với Hub và audit.
- Đã sửa compare cho snapshot chỉ có `r2_key`: lấy mẫu tối đa 10 dòng bằng file tạm, dùng row counts đã lưu và không tải toàn bộ object R2 vào RAM. Kiểm thử fake R2 bao phủ upload object, xóa `csv_bytes` khỏi DB và compare lại; đường download tiếp tục dùng presigned link 300 giây.
- Rà soát thêm coverage cho event chưa được ghi ở các API mutation; không tự chế lịch sử cũ.

**Kiểm thử:** preview có blocker, pending review, hết hạn/stale, bấm đôi, mất response execute, CSV có dấu, tải lại snapshot, R2 unavailable, selective split giữ snapshot gốc, owner và role trên download.

**Đạt khi:** CSV mới bao phủ đúng nguồn; snapshot cũ tải lại được; audit không bị lẫn với lịch sử run/export.

### CP11 — Nghiệm thu toàn hệ thống và chuyển giao diện

- Chạy test/build đầy đủ, browser E2E với API thật, kiểm thử thị giác và responsive.
- Chạy lại đúng kịch bản CP00; so sánh dữ liệu chuẩn hóa/quan hệ thành viên/giá trị trường và thống kê, không chỉ nhìn screenshot.
- Dùng database test riêng; trước áp migration môi trường đang dùng cần backup và diễn tập khôi phục.
- Chuyển cờ sang UI mới sau khi đạt tiêu chí. Nếu lỗi, quay lại shell cũ; backend additive phải còn tương thích với UI cũ trong giai đoạn này.
- Lưu report gồm commit, dataset hash, run ID, quyết định đã chọn, ảnh, trace, CSV và lỗi còn lại. Đây là artifact nghiệm thu, không dùng dữ liệu khách hàng thực.

**Đạt khi:** không mất dữ liệu, không vượt quyền, không đổi kết quả gộp ngoài ý muốn; mọi luồng cốt lõi hoạt động và rollback UI đã thử thành công.

### CP12 — Phần mở rộng sau bản đầu

Mỗi nhóm sau cần thiết kế backend, test riêng và được lập phạm vi triển khai riêng: Series/so sánh tối đa 5 phiên; dashboard chất lượng đầy đủ; lịch sử telemetry chi tiết; lưu draft choices đa thiết bị; upload lớn nối xuyên suốt vào resolution; export job nền/nhiều phiên/XLSX/JSONL; notifications bền vững; lưu trữ lạnh và phục hồi; chia sẻ phiên/cộng tác.

Không cam kết số dòng tối đa, thời hạn link, số export đồng thời hoặc “lưu vĩnh viễn” theo số minh họa trong concept trước khi có đo đạc và hỗ trợ hạ tầng.

## 5. Chiến lược kiểm thử

### Các lớp kiểm thử và bằng chứng

| Lớp | Công cụ/cách thực hiện | Trách nhiệm |
|---|---|---|
| Component/hook | Vitest + Testing Library hiện có | State, accessibility, race condition, draft, quyền hiển thị |
| Backend/service/API | pytest hiện có | Ownership, capability, state transition, schema, CSV, preview/split/idempotency |
| Browser có API giả lập | Đề xuất bổ sung Playwright, chưa có trong package hiện tại | Trạng thái lỗi/timing khó tái hiện, response đảo thứ tự, snapshot UI |
| Browser + API thật | Playwright với FastAPI/SQLite test và worker thật | Luồng đăng nhập → upload → review → export → tải lại |
| Manual/UAT | Bàn phím, responsive, review liên tục, screenshot | Dễ hiểu, không mất ngữ cảnh, không che dữ liệu/CTA |
| Dữ liệu/hiệu năng | API timing, browser trace, đọc CSV | Không mất/trùng record; render/list/poll không suy giảm đáng kể |

Giả lập Gemini/R2 trong test tự động để có kết quả ổn định. Kiểm thử tích hợp R2 thật chỉ chạy khi có môi trường test riêng; không xem test mock là bằng chứng cloud đã hoạt động.

### Ma trận kịch bản bắt buộc

| ID | Kịch bản | Mong đợi |
|---|---|---|
| E01 | Tài khoản chưa có run | Empty state và CTA đúng quyền |
| E02 | Upload nhiều CSV hợp lệ | Tổng nguồn đúng; tạo một run; start riêng |
| E03 | Một file sai trong batch | Lỗi rõ, không có run nửa vời trong DB |
| E04 | Đổi file/đóng drawer khi đang nạp | Draft còn khi trang mở; dữ liệu phiên cũ không thay đổi |
| E05 | Reload run UPLOADED | Tiếp tục xử lý được, không phải nạp lại |
| E06 | Worker restart giữa chừng | Retryable failure có nhãn và đường phục hồi đúng |
| E07 | Xem A trong khi B chạy | UI của A nhất quán; status bar vẫn cập nhật B |
| E08 | A→B nhanh, response A về sau | A không ghi đè B |
| E09 | Hub hơn 25 run, URL và Back/Forward | Không mất run/filter; không cấp quyền qua link |
| E10 | Review có nhiều record/giá trị khác | Chọn nguồn đúng, xem đủ record và bằng chứng |
| E11 | Save thiếu trường → chuyển sang merge | Draft giữ được, export còn bị chặn nếu thiếu |
| E12 | Không gộp/hard constraint/staged conflict | Không vượt chặn, item an toàn còn nguyên |
| E13 | Còn review nhưng muốn xuất | Cảnh báo/xác nhận; phần chưa duyệt giữ riêng |
| E14 | Preview hết hạn/stale, execute bấm đôi | Bị từ chối đúng hoặc retry idempotent, không xuất sai |
| E15 | Tải lại/compare/split snapshot DB và R2 | Snapshot gốc bất biến, snapshot con có lineage |
| E16 | Viewer, tài khoản khác, hết login, CSRF sai | UI phản hồi đúng, API từ chối đúng, không rò dữ liệu |
| E17 | Gemini timeout | Review thủ công vẫn tiếp tục được |
| E18 | Mobile, bàn phím, 200% zoom, reduced motion | Hoàn tất được thao tác chính, focus và CTA không bị che |
| E19 | Mất mạng/reconnect, server 5xx | Giữ ngữ cảnh; retry an toàn; không suy diễn pipeline FAILED |
| E20 | Đổi theme/đăng xuất/đăng nhập tài khoản khác | Không lẫn phiên, dữ liệu hoặc draft của tài khoản trước |

### Bất biến dữ liệu cần kiểm tra trên CSV

- Mỗi source record đầu vào được bao phủ đúng một lần trong output; kiểm tra cả số lần xuất hiện, không chỉ số ID duy nhất.
- Tổng `record_count` của các dòng xuất bằng số bản ghi đầu vào; `master_record_id` duy nhất.
- Giá trị hợp nhất khớp nguồn đã chọn; không vi phạm negative link/hard constraint.
- Không loại bỏ record chưa review; phân biệt số item review với số record giữ riêng.
- Thao tác split không ghi đè snapshot gốc; số bản ghi và quan hệ nguồn của snapshot con đúng.

### Lệnh kiểm tra dự kiến tại cổng nghiệm thu

Chạy tại thư mục gốc repo, sau khi môi trường test đã được chuẩn bị:

```powershell
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
uv run --python .venv python -m pytest backend/tests/ -q
uv run --python .venv python -m ruff check backend/src backend/tests backend/migrations
```

`lint` frontend hiện là `tsc --noEmit`, chưa phải một bộ ESLint. Test E2E đề xuất cần bổ sung script/config/dependency và job CI trước khi đưa lệnh của nó vào checklist bắt buộc.

Tiêu chí hiệu năng ban đầu: trên cùng máy/dataset so với CP00, p95 list/detail và thời gian có nội dung hữu ích không tăng quá 20%; điều chỉnh ngưỡng sau baseline nếu độ nhiễu đo lớn. Browser chỉ lấy trang cần xem; tối đa một vòng poll cho mỗi tài nguyên đang theo dõi trong một workspace; không tải toàn bộ lịch sử/record để dựng Hub. Đây là tiêu chí đề xuất, chưa phải kết quả đo.

## 6. Thứ tự thực hiện và định nghĩa hoàn tất

Thứ tự: CP00 → CP01 → CP02 → CP03 → CP04 → CP05 → CP06 → CP07 → CP08 → CP09 → CP10 → CP11. CP12 là backlog sau nghiệm thu bản đầu.

Ưu tiên nếu chia đợt phát hành:

1. Đợt nền: baseline, contract/quyền, shell, URL và Hub.
2. Đợt trải nghiệm chính: drawer upload, processing, overview và review ba cột.
3. Đợt hoàn thiện: export/history/audit theo API sẵn sàng, E2E, UAT và chuyển mặc định.

Hoàn tất bản đầu khi giao diện mới dùng dữ liệu thật, luồng nghiệp vụ cũ được bảo toàn, kiểm tra quyền và dữ liệu đạt, phiên cũ truy cập được theo quyền, responsive dùng được và có rollback UI. Không coi việc dựng đúng ảnh mockup là đã hoàn tất hệ thống.

## 7. Kết quả kiểm tra trong lần lập kế hoạch

- Đã đối chiếu ba tài liệu với code frontend/backend, tests, CI và báo cáo manual hiện có.
- `npm --prefix frontend run lint`: đạt (TypeScript).
- `npm --prefix frontend test`: đạt **19/19 tests, 3/3 files** khi chạy lại ngoài sandbox. Lần đầu trong sandbox dừng ở khâu khởi động do `spawn EPERM`, không phải assertion thất bại.
- Chưa chạy backend suite, production build hoặc browser E2E trong lần lập kế hoạch này; chưa thể kết luận toàn hệ thống đang đạt.
- Chỉ bổ sung tài liệu kế hoạch; chưa triển khai UI/migration/API mới. Các thay đổi đang có của người dùng trong thư mục scripts được giữ nguyên.

## Cập nhật triển khai GĐ2 (2026-10-07)

Phần kết quả ở trên là snapshot lúc lập kế hoạch, được thay bằng trạng thái hiện tại sau khi người dùng duyệt làm tiếp: API draft/chunk/resume/validate/replace/commit; file cũ chỉ được thay sau khi bản mới qua kiểm tra; không giới hạn dung lượng hoặc số dòng từng file ở tầng ứng dụng; tối đa 10 file mỗi phiên. Hub có cursor search/filter, rename và archive/restore theo owner/capability. Dung lượng thực vẫn phụ thuộc disk/R2, proxy, database và worker; chưa benchmark nhiều triệu dòng. Retention/expiry chưa làm vì thời hạn và purge policy còn mở.

Xác minh hiện tại: 25 backend test trọng tâm pass; frontend 32 test pass, `npm run lint`, Ruff cho file CP2 và production build pass. Backend suite tổng thể có 277 pass và 10 lỗi nền ngoài CP2 (LLM settings/audit, pipeline scoring/address và selective unmerge). Chưa thực hiện browser E2E hoặc pixel comparison với HTML tham chiếu.
