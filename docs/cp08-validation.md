# Kiểm tra CP08 — Tổng quan và tổng hợp nhiều file

## Quy ước số liệu

Tổng quan lấy số từ `GET /api/v1/mdm/runs/{run_id}/overview`. `record_count` là số dòng đầu vào; `file_count` là số file của run; `source_counts` cộng các bản ghi theo nguồn qua toàn bộ file; `counts.auto` là cụm đang ở hàng đợi gộp; `counts.review` là item review đã gom nhóm cộng proposal cần review; `counts.separate` là số bản ghi dự kiến giữ riêng; `history_count` là số snapshot CSV; `candidate_count` là số cặp được đối chiếu. Các nhóm có đơn vị khác nhau, không cộng thành một “tổng khách hàng”.

## Kiểm tra tự động

Bài kiểm thử `test_three_csv_files_are_processed_and_exported_as_one_run` đi hết luồng UploadSession: tải lên ba CSV CRM/ERP/BILL, kiểm tra, commit thành một run, start pipeline, đọc overview rồi xuất một CSV. Nó xác nhận overview có 3 file/3 dòng, mỗi nguồn có một dòng, `separate=3`, và CSV đầu ra chứa đủ ba mã bản ghi. Fake R2 trong bài test xác nhận CSV được ghi thành object, payload nhị phân được xóa khỏi DB và endpoint compare vẫn đọc được snapshot đó.

Các kiểm tra liên quan:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_upload_sessions.py backend/tests/test_mdm/test_session_contract.py backend/tests/test_mdm/test_sessions_hub.py backend/tests/test_mdm/test_rbac.py backend/tests/test_mdm/test_audit.py -p no:cacheprovider -q
.venv\Scripts\ruff.exe check backend/tests/test_mdm/test_upload_sessions.py backend/tests/test_mdm/test_session_contract.py backend/tests/test_mdm/test_sessions_hub.py backend/tests/test_mdm/test_rbac.py backend/tests/test_mdm/test_audit.py backend/src/masterone/api/mdm_resolution.py backend/src/masterone/api/mdm_runs.py backend/src/masterone/mdm/resolution_import.py backend/src/masterone/mdm/resolution_items.py backend/src/masterone/auth/security.py
```

## Kiểm tra thủ công

1. Tạo một phiên với ba CSV có nguồn CRM, ERP và BILL; sau khi validate/commit, xác nhận mỗi file còn trong cùng một phiên.
2. Mở Tổng quan: tổng bản ghi/file và phân bố nguồn phải cộng từ cả ba file.
3. Chạy phiên; khi hoàn tất, kiểm tra các KPI Chờ gộp/Cần review/Giữ riêng và các nhóm A/C/D có đúng đơn vị, không biến thành phần trăm hay số thực thể cuối.
4. Xem trước rồi xuất CSV; xác nhận tất cả source record IDs từ cả ba file có mặt trong cùng một snapshot.
5. Mở lịch sử; số snapshot tăng một và tải lại đúng CSV vừa xuất.

## Giới hạn

Đã kiểm tra luồng tổng hợp bằng fixture ba dòng. Chưa benchmark nhiều triệu dòng; không tuyên bố hiệu năng quy mô đó chỉ dựa trên kiểm thử chức năng.
