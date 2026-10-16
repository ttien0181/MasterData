# Dọn workspace ngày 16/10/2026

Đã bỏ **3.660 file sinh tự động**, giải phóng **573,98 MB** ngoài đợt dọn
database benchmark trước đó. Các file được đối chiếu kích thước và thời
gian sửa trước khi xóa; file Git theo dõi được bảo vệ, ngoại trừ năm con
trỏ pytest đã xác nhận trỏ tới ổ `D:` của máy cũ.

## Đã dọn

| Loại | Dung lượng xấp xỉ |
|---|---:|
| Sandbox pytest ở `benchmark/.cache/` | 289,75 MB |
| Cache biên dịch Turbopack | 274,08 MB |
| Python bytecode cache ngoài thư viện và runtime assets | 7,80 MB |
| Bản sao package sinh bởi setuptools ở `backend/build/` | 1,54 MB |
| Cache TypeScript, pytest, ruff và con trỏ pytest cũ | 0,81 MB |

Giữ `.gitkeep` của cache benchmark, báo cáo kiểm thử, dữ liệu và model,
`.venv`, `node_modules`, metadata package, cấu hình `.env`, migrations,
toàn bộ nguồn ứng dụng và các file build frontend cần để chạy hệ thống.
Trong `.next`, chỉ dọn cache Turbopack và cache TypeScript; giữ cả
`.previewinfo`, `.rscinfo` và cấu hình devtools.

Không thay đổi dịch vụ, container, database hoặc biến môi trường.
Thư mục `.tmp-review-20261013` và một thư mục trong `artifacts/merge-baseline`
bị từ chối quyền đọc được giữ nguyên.

## File đã chuyển chỗ, giữ nguyên nội dung

| Đường dẫn cũ ở gốc dự án | Đường dẫn mới |
|---|---|
| `apply_refactor.py` | `scripts/archive/apply_refactor.py` |
| `refactor.py` | `scripts/archive/refactor.py` |
| `refactor_service.py` | `scripts/archive/refactor_service.py` |
| `temp_stand.py` | `scripts/archive/temp_stand.py` |
| `test_gemini.py` | `scripts/archive/test_gemini.py` |
| `README_boilerplate.md` | `docs/archive/README_boilerplate.md` |
| `LNTT (1).mp4` | `presentation/media/LNTT (1).mp4` |

Không tìm thấy tham chiếu đến những đường dẫn cũ trong nguồn ứng dụng,
CI, cấu hình hoặc tài liệu đã quét. Hash SHA-256 trước/sau được lưu trong
nhật ký để xác nhận nội dung không thay đổi. Có thể chuyển từng file về
đường dẫn cũ nếu cần dùng lại quy trình lịch sử.

## Kiểm chứng và duy trì

Nhật ký chi tiết: `artifacts/cleanup/workspace-20261016T081302.json`.
Kết quả kiểm chứng được lưu cạnh nhật ký dưới tên `*.verification.json`.
Đối chiếu inventory để bảo đảm chỉ các file trong kế hoạch bị xóa/chuyển,
và kiểm tra hash nguồn, cấu hình, dữ liệu, model cùng build frontend.

Backend và frontend không phản hồi tại `localhost:8001/health` và
`localhost:3000` trước khi dọn; Docker daemon cũng không chạy. Vì vậy
kiểm chứng lần này dựa trên file và đường dẫn import, không phải kiểm thử
trên dịch vụ đang chạy. Không khởi động lại hệ thống chỉ để dọn cache.

Đã thêm quy tắc Git bỏ qua `.pytest-tmp*` và `.tmp-review*` để file tạm
riêng của máy không tiếp tục xuất hiện trong thay đổi dự án. Giữ script
thử nghiệm trong `scripts/`, tài liệu cũ trong `docs/archive/` và media
trong `presentation/media/` để thư mục gốc gọn hơn. Với benchmark nặng,
áp dụng [chính sách artifacts](artifact-retention.md).
