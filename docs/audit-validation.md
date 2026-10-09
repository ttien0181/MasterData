# Kiểm tra Audit theo phiên

## Hành vi đã triển khai

- `GET /api/v1/mdm/runs/{run_id}/audit` đọc event theo trang, sắp xếp mới nhất trước và hỗ trợ lọc `action`, `pair_id`, `proposal_id`.
- Route kiểm tra quyền sở hữu run và capability `read_audit`. Viewer nhận 403; Steward, Senior Steward, Policy Admin và Auditor được đọc audit run thuộc tài khoản của họ.
- Workspace có mục Audit chỉ đọc, hiển thị tác nhân, thời điểm, loại thao tác, cặp/đề xuất liên quan và JSON chi tiết.
- Các event mới được ghi nhận cho tạo phiên, bắt đầu/chạy lại phiên và xuất CSV. Những thao tác duyệt/từ chối, sửa Golden, preview, đổi tên và archive/restore vốn đã ghi event vẫn được hiển thị.
- Không tạo lại audit lịch sử trước ngày triển khai.

## Kiểm tra tự động

Từ thư mục gốc repo:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_audit.py backend/tests/test_mdm/test_rbac.py backend/tests/test_mdm/test_sessions_hub.py -p no:cacheprovider --basetemp=.pytest-tmp-audit -q
.venv\Scripts\ruff.exe check backend/src/masterone/api/mdm_resolution.py backend/src/masterone/api/mdm_runs.py backend/src/masterone/mdm/resolution_import.py backend/src/masterone/mdm/resolution_items.py backend/src/masterone/auth/security.py backend/tests/test_mdm/test_audit.py backend/tests/test_mdm/test_rbac.py
```

Từ `frontend`:

```powershell
npm test -- --run
npm run lint
```

## Kiểm tra thủ công

1. Với Steward, mở một run đã có thao tác; xác nhận Audit tải được, tổng số event đúng, trang kế tiếp/lùi hoạt động và event mới nhất đứng đầu.
2. Lọc theo action, pair ID và proposal ID; xóa lọc để trở lại danh sách đầy đủ. Mở “Chi tiết sự kiện” để xem metadata của event.
3. Tạo run, start/retry, duyệt/từ chối hoặc sửa Golden, archive/restore và export CSV; xác nhận từng thao tác mới xuất hiện sau khi làm mới Audit.
4. Đăng nhập Viewer và thử gọi route bằng DevTools: API trả 403, còn mục Audit không xuất hiện trong dock.
5. Đăng nhập tài khoản Steward khác rồi mở run của người đầu: API trả 404 để không tiết lộ run ngoài phạm vi owner.
6. Thử light/dark, màn hình hẹp và bàn phím; form lọc, nút phân trang và disclosure chi tiết vẫn dùng được.

## Phạm vi còn lại

Chưa có lọc theo người/thời gian, export audit, retention policy hoặc browser E2E. Không có migration mới cho checkpoint này.
