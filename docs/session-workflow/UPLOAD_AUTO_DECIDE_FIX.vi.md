# Sửa upload và tự phân loại cặp — 11/10/2026

## Hành vi

Với workflow v2, worker hoàn tất PREPARE rồi tự xếp DECIDE vào hàng đợi trong
cùng transaction. Tác vụ không phụ thuộc việc giữ trang trình duyệt mở. Kết quả
dừng ở DECIDE_REVIEW để người dùng duyệt; không tự chốt quyết định hoặc chạy CLUSTER.

`SESSION_AUTO_DECIDE_ENABLED=true` là mặc định. Đặt `false` và khởi động lại API/worker
để giữ bước bấm nút bắt đầu phân loại. Actor phải còn hoạt động và có capability
`start_run`; nếu quyền không còn phù hợp thì giữ DATA_READY.

## Nguyên nhân và thay đổi

Nhánh DECIDE song song từng rút gọn cặp INSUFFICIENT_EVIDENCE thành chỉ trường
`decision`, theo giả định của luồng FULL cũ. Workflow v2 cần lưu mọi candidate nên
StageRunner đọc `blocking_reasons` đã bị mất và phát sinh KeyError. Worker thử lại
ba lần rồi chuyển RETRYABLE_FAILED.

Sửa `_decide_rule_batch` để trả đầy đủ payload như nhánh tuần tự. Luồng FULL vẫn
bỏ cặp thiếu bằng chứng tại consumer cũ. Tự khởi chạy DECIDE sử dụng khóa phiên,
revision, operation và task hiện có; hoàn tất attempt PREPARE trước khi dùng lại
task cho DECIDE. Test kiểm tra cả nhánh tuần tự/song song và chuyển tiếp tự động.

## Kiểm chứng bản đang chạy

Project QA: `masterone-workflow-live`, giao diện `http://127.0.0.1:13018`.
Chỉ cập nhật API/worker; giữ database và cấu hình storage/provider đang chạy.

- Phiên người dùng `21c9a723-8aa0-44f5-9083-dd4edc99c6ef` được xếp lại đúng operation
  DECIDE, giữ các attempt thất bại và thêm audit retry; không upload lại.
- 1.651 bản ghi, 17.334 candidate và 17.334 cặp đã công bố; trong đó 15.220 cặp
  thiếu bằng chứng. Trạng thái DECIDE_REVIEW/COMPLETED, chưa tạo proposal.
- Upload synthetic mới chuyển DATA_PREPARING → DECIDE_RUNNING → DECIDE_REVIEW
  mà không gọi `/start-decide`; hai bản ghi tạo một cặp, không có cluster/golden.
- Browser đăng nhập bằng tài khoản người dùng, mở tab phân loại và hiển thị 25
  dòng đầu cùng bộ đếm 17.334 cặp; không có thông báo lỗi.
- Global LLM switch giữ `false`.

Evidence: `artifacts/session-workflow/20261011-workflow-01/fix-upload-20261011T053303Z-42d38a4f/`
gồm update, recovered-user-run, auto-upload-smoke và browser-smoke JSON.

43 test phân loại/workflow/queue PASS ở
`artifacts/merge-baseline/20261011T053053Z-805zluoc/`.
Ruff trên các file sửa PASS.

Bộ test backend đầy đủ: **1.166 PASS, 109 SKIP**, không có test FAIL;
thời gian 640,98 giây. Evidence cuối:
`artifacts/merge-baseline/20261011T053119Z-6xc90dz4/`.
Các ca SKIP không được tính là PASS.

Các lượt test timeout trước đó đã được giữ riêng trong evidence: test mới gọi
reader PostgreSQL bằng fixture SQLite. Test chuyển tiếp được sửa để dùng compute
và publication thật trên fixture SQLite; worker PostgreSQL được kiểm chứng trên
QA đang chạy như mô tả trên. Đây không phải lỗi reader trên PostgreSQL.
