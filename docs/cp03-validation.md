# Kiểm tra GĐ3 — overview, processing và status bar

## Phạm vi

GĐ3 hoàn thiện trạng thái/phase và KPI dựa trên API hiện có. `GET /mdm/runs?run_status=QUEUED,RUNNING` trả các run thuộc owner hiện tại cùng `phase`; thanh đáy chỉ tải tối đa 5 active run gần nhất nhưng hiển thị tổng số chính xác. Overview của run đang mở poll tuần tự; khi lỗi mạng UI giữ dữ liệu gần nhất và đánh dấu stale. Không thêm migration, không thay pipeline hoặc tính phần trăm giả.

## Kiểm tra tự động

Chạy từ thư mục gốc repo:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_sessions_hub.py backend/tests/test_mdm/test_rbac.py -p no:cacheprovider -q
.venv\Scripts\ruff.exe check --no-cache backend/src/masterone/api/mdm_runs.py backend/tests/test_mdm/test_sessions_hub.py
```

Chạy tại `frontend`:

```powershell
npm test -- --run
npm run lint
npm run build
```

Kết quả GĐ3: 17 backend test Hub/RBAC pass; Vitest 7 file / 35 test pass; TypeScript lint, Ruff và Next production build pass.

## Kiểm tra thủ công

1. Bắt đầu hai phiên liên tiếp, để một phiên `RUNNING` và một phiên `QUEUED`; thanh trạng thái phải hiện tổng 2. Mở danh sách để thấy tên/trạng thái/phase và chọn một phiên để chuyển ngữ cảnh.
2. Khi đang xem phiên cũ, xác nhận thanh trạng thái vẫn liệt kê phiên khác đang chạy. Viewer chỉ xem được danh sách, không có nút thao tác tạo hoặc sửa.
3. Đang xem run `RUNNING`, ngắt mạng trong DevTools. UI phải giữ phase/trạng thái đã nhận, hiện thông báo chưa cập nhật; không đổi run thành FAILED. Bật mạng lại và xác nhận status tự cập nhật.
4. Quan sát Network: overview chỉ được gọi cho run đang mở, không gọi một overview riêng cho từng active run. Khi lỗi, khoảng cách poll tăng dần; khi thành công, trở về chu kỳ bình thường.
5. Với `UPLOADED`, `QUEUED`, `RUNNING`, `RETRYABLE_FAILED`, `FAILED`, xác nhận nhãn và hành động tương ứng; phase không xác định phải dùng nhãn fallback, không render phần trăm.
6. Chuyển light/dark và viewport mobile: panel active run không bị thanh đáy hoặc màn hình cắt; có thể thao tác bằng bàn phím và trạng thái stale đọc được.

Chưa chạy browser E2E hoặc kiểm tra pixel với HTML mẫu; hai việc này thuộc GĐ7.
