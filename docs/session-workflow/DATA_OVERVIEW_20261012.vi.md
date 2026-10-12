# Tổng quan dữ liệu và phân trang — 12/10/2026

## Phạm vi và kiểm toán

Đọc README, tài liệu session workflow, thiết kế tổng quan trước đó, Compose hiện có và code API → PREPARE → artifact → bảng frontend. Tài liệu `DATA_OVERVIEW_REDESIGN.vi.md` trước đây mô tả một candidate khác: source đầu phiên này vẫn hiển thị JSON và endpoint workflow mặc định 25 dòng. Bản sửa hiện tại áp dụng cho luồng `/app/sessions/{run_id}/data`.

Không tạo hay thay đổi Compose trong lần sửa này. Build và cập nhật đúng ba service backend, worker, frontend của project `p119-session-qa`, bằng `docker-compose.yml` hiện có và `.env.qa` của runtime. Không đổi key, thuật toán matching, threshold hoặc dữ liệu phiên cũ. Workspace có nhiều thay đổi kế thừa chưa commit; không reset, commit hay push.

## Giao diện và ý nghĩa chỉ số

- Bốn thẻ: tổng bản ghi trong phiên, số nguồn dữ liệu, số tệp đã kiểm tra, số bản ghi có ít nhất một cảnh báo. Bản ghi có nhiều cảnh báo chỉ được tính một lần ở thẻ tổng.
- Phân bố số lượng/tỷ lệ theo nguồn, tỷ lệ có thông tin cho sáu trường, danh sách loại cảnh báo có số lượng và nhãn tiếng Việt.
- Độ đầy đủ tính trên dữ liệu gốc khác null, rỗng hoặc chỉ có khoảng trắng. Giá trị sai định dạng vẫn có thể được tính là có thông tin; đây không phải điểm hợp lệ hoặc điểm chất lượng tổng hợp.
- Bảng có STT, nguồn/mã ngoài, họ tên, điện thoại, email, địa chỉ, cảnh báo và chi tiết. Ngày sinh, giấy tờ và trường bổ sung nằm trong chi tiết dạng nhãn/giá trị. Chuyển dữ liệu gốc/chuẩn hóa mà không thay đổi dữ liệu server.
- 10 bản ghi mỗi trang; đầu/trước/sau/cuối, khoảng bản ghi và số trang. Khi lỗi mạng, giữ trang đã tải và cho phép thử lại bằng nút thử lại hoặc nút điều hướng vừa thất bại.
- Bảng cuộn ngang trong vùng riêng; giao diện thích ứng 390px và hỗ trợ chủ đề sáng/tối.

Chọn thống kê có thông tin/cảnh báo theo cách profiling của [Microsoft Purview](https://learn.microsoft.com/en-us/purview/unified-catalog-data-quality-profiling) và [Ataccama ONE](https://docs.ataccama.com/ataccama-one-agentic/latest/data-quality/understand-profiling-results.html). Không suy diễn số người trùng từ giá trị trường trùng nhau.

## API, tính toàn vẹn và hiệu năng

Endpoint: `GET /api/v1/mdm/runs/{run_id}/workflow/data?offset=0&limit=10`. Mặc định 10 dòng, giới hạn API tối đa 100 cho tương thích; giao diện luôn gửi 10. Giữ `samples`, `total`, `next_cursor` và hỗ trợ cursor cũ; bổ sung `samples.offset`. SQL sắp xếp theo ID, OFFSET/LIMIT, không đưa toàn bộ bản ghi về frontend.

PREPARE mới tính thống kê toàn phiên trong một lượt duyệt. Lưu artifact `PROFILE` nhỏ cùng giao dịch với PREPARE, chứa profile, ID và hash PREPARE. GET chỉ đọc metadata PREPARE và bản thống kê khớp ID/run/hash, rồi kiểm tra hash độc lập của PROFILE. Không tải lại payload bản ghi đầy đủ để hiển thị 10 dòng. Bản thống kê hỏng trả 409, không bị thay bằng fallback âm thầm.

Phiên cũ chưa có PROFILE vẫn kiểm tra đầy đủ hash PREPARE trước khi dẫn xuất thống kê. `warm_profile` là thao tác backfill tường minh, kiểm tra snapshot cũ trước khi tạo bản thống kê; không chạy trong GET và không sửa PREPARE, revision hoặc kết quả engine. Đã backfill tuần tự hai phiên 10k liên quan: phiên kiểm tra mới và phiên trong ảnh người dùng. Các phiên cũ khác có thể chạy backfill này khi cần. Hai lần backfill mất 125,125 và 101,875 giây do đọc snapshot từ DB hiện tại; đây là thao tác một lần.

Fast path xác minh bản thống kê đã seal và tham chiếu hash của PREPARE; không tái hash toàn bộ payload PREPARE mỗi lần đọc. Engine vẫn kiểm tra snapshot đầy đủ trước khi dùng. Không có migration schema mới.

Trước khi backfill, mỗi lần đọc phiên cũ chưa có PROFILE vẫn tải snapshot đầy đủ và có thể chậm. Backfill hiện tại chỉ áp dụng cho hai phiên được kiểm tra; không tự sửa các phiên khác trong GET.

## Dữ liệu kiểm tra thực tế

Chọn một TC trong các snapshot PREPARE 10k đầy đủ khả dụng của thư mục người dùng. Bản download hiện có một snapshot đủ điều kiện: `TC4_hard_non_match/10k/seed-7`. Lấy tối đa 10.000 dòng và chỉ cột `raw__`, bỏ truth/entity/cluster label, chia hai CSV 5.000 dòng. Không chọn thêm TC, không chạy LLM hoặc phân loại lại để kiểm tra trang này.

- Phiên thử: `9a1dba38-4491-448e-9fb2-6c3507efdecb`; upload thực qua API và PREPARE bằng worker.
- 10.000 bản ghi, 3 nguồn, 2 tệp, 1.758 bản ghi có cảnh báo.
- Nguồn: CRM 3.384, CSV 3.341, ERP 3.275.
- Có thông tin: họ tên/điện thoại/địa chỉ 100%; email 76,3%; ngày sinh 82,42%; giấy tờ 92,16%.
- Trang 1, 2 và 1.000 có đúng 10 dòng; thống kê không đổi giữa trang. So sánh số bản ghi có cảnh báo với aggregate DB và độ đầy đủ với dữ liệu gốc được nạp.
- Phiên cũ `3449137c-8f4d-41cc-9152-fa12768aa355` vẫn đọc được đủ 10.000 dòng và thống kê mới.

Thời gian đọc từng trang sau tối ưu nằm trong `artifacts/data-overview-20261012/api.json`; đây là thời gian HTTP với DB runtime hiện tại, không phải cam kết SLA.

## Bằng chứng

- Backend toàn repo trên source cuối: 951 passed, 2 skipped, 490,45 giây. Log `artifacts/data-overview-backend-final.log`.
- Test backend tập trung: 31 passed, gồm phân trang qua mốc 100, trường rỗng, thống kê toàn phiên, hash hỏng, fast path không chọn payload PREPARE, rollback cùng giao dịch, backfill idempotent và giữ snapshot cũ. Log `artifacts/data-overview-backend-scoped-final.log`.
- Frontend: 79 passed; TypeScript và Ruff cho các file liên quan đạt. Log `artifacts/data-overview-frontend-tests.log`.
- E2E regression dùng API mock: 6 passed. Log `artifacts/data-overview-e2e-final.log`.
- Trình duyệt với API/DB/worker thật: không hiển thị JSON; đúng 10 dòng; chuyển đầu/sau/cuối/trước; chuyển chuẩn hóa, mở chi tiết; chủ đề tối và màn hình 390px không tràn toàn trang, không có lỗi JavaScript. `artifacts/data-overview-20261012/browser.json` và ảnh desktop/dark/mobile đã được xem trực tiếp.
- Build/deploy Docker: `artifacts/data-overview-final-build.log`, `artifacts/data-overview-final-deploy.log`; source API/worker được đóng gói, không dựa vào bind mount source.
- Hash ba module data profile, workflow artifact và workflow API trong cả API/worker khớp byte với source workspace. Frontend cuối có chỉnh độ tương phản cảnh báo trong chế độ tối, đã build và chụp/kiểm tra lại qua `artifacts/data-overview-browser-final.log`.

Lượt kiểm thử toàn repo với `APP_ENV=test` có một lỗi `test_local_next_dev_origin_can_register`, vì test yêu cầu origin development. Đây là cấu hình harness; không mở rộng CORS runtime để vượt test. Lượt kiểm thử cuối dùng development, bỏ env/key provider thật; kết quả cuối được lưu trong `artifacts/data-overview-backend-final.log`.

Runtime hiện tại là Docker local dùng `APP_ENV=test`, frontend localhost:3000 và API localhost:8001. Bản sửa này đã kiểm chứng luồng dữ liệu local; không chứng nhận các gate HTTPS/backup/monitoring của một deployment public.
