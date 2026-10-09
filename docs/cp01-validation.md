# Kết quả kiểm tra CP01 — 06/10/2026

## Phạm vi

Thay đổi hợp đồng auth/capabilities, guard API đang chạy, adapter phiên và quyền giao diện hiện tại. Không đổi schema database, matcher/pipeline hoặc giao diện light/dark. Thiết kế hai theme vẫn thuộc CP02.

## Kiểm tra nền trước thay đổi

Lần chạy backend đầu tiên trong sandbox: 260 passed, 20 failed. Các lỗi gồm cấu hình/model LLM cũ, kỳ vọng thuật toán, migration dùng SQL PostgreSQL trên SQLite, fixture golden thiếu id, cấu hình R2 của môi trường và quyền thư mục tạm Windows.

Đã cô lập fixture API khỏi R2/Gemini thật. Lần chạy toàn backend ngoài sandbox sau thay đổi: **269 passed, 12 failed**. Mười hai test còn lỗi đều đã có tên trong danh sách lỗi lần kiểm tra nền, không phải test mới của CP01:

- `test_llm/test_phase_l0.py`: 4 test còn tham chiếu `Settings.llm_enabled` đã không có.
- `test_llm/test_phase_l4.py::test_flush_to_db_no_raise_on_factory_error`: thiếu model `LLMRequest` trong module đang import.
- `test_mdm/test_document_pipeline.py::test_different_contact_is_df_while_name_conflict_goes_to_d`: kỳ vọng MATCH_CANDIDATE, thực tế REVIEW.
- `test_mdm/test_document_pipeline.py::test_selective_unmerge_preserves_other_members_and_selected_value`: fixture truyền record thiếu `id` vào `build_golden`.
- `test_mdm/test_migrations.py`: 2 test SQLite gặp SQL `field_evidence::text` của PostgreSQL trong migration có sẵn.
- `test_mdm/test_regression_t1_t20.py::test_T11_address_admin_reform_not_penalised` và `test_regression_t21_t28.py::test_T31_address_same_ward_resolved_score_at_least_MS`: phép `&` trên list.
- `test_mdm/test_selective_integration.py::test_same_source_duplicates_and_legacy_phone_are_candidates`: kỳ vọng MATCH_CANDIDATE, thực tế REVIEW.

Log tại `artifacts/cp01-backend-tests.txt`. Không sửa thuật toán/migration lịch sử để làm xanh các lỗi ngoài phạm vi CP01.

## Kiểm tra mã

- Ruff trên tất cả file Python đã sửa/thêm cho CP01, kể cả fixture và test hồi quy có điều chỉnh role: đạt.
- Ruff toàn `backend/src backend/tests backend/migrations`: còn **140 lỗi**, chủ yếu các file có sẵn ngoài phần logic CP01; log `artifacts/cp01-ruff-full.txt`.
- Kiểm tra TypeScript: đạt.
- Frontend: **28/28 tests, 4/4 files đạt** ở lần xác nhận cuối sau chỉnh nhãn KPI.
- Production build: **đạt**. Lần đầu sandbox không tải được Google Fonts; lần chạy lại ngoài sandbox thành công.
- Bộ backend nghiệm thu CP01: **34/34 tests đạt**; log `artifacts/cp01-targeted-tests.txt`. Bao gồm auth, RBAC, session contract, schema upload, toàn bộ resolution-items, preview stale/idempotency và selective unmerge tạo snapshot mới.

Lệnh nghiệm thu backend có thể chạy lại:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_mdm/test_auth.py backend/tests/test_mdm/test_rbac.py backend/tests/test_mdm/test_session_contract.py backend/tests/test_mdm/test_resolution_import_schemas.py backend/tests/test_mdm/test_resolution_items.py backend/tests/test_mdm/test_document_pipeline.py::test_preview_detects_changed_pair_and_execute_is_idempotent backend/tests/test_mdm/test_document_pipeline.py::test_selective_unmerge_creates_new_snapshot_and_negative_edge -q -p no:cacheprovider --tb=short
```

Đây là bộ kiểm thử tập trung cho thay đổi CP01; không thay thế kết quả full suite 269 passed / 12 failed đã công bố ở trên. Cổng nghiệm thu toàn repo chưa xanh. Các cảnh báo thư viện Starlette/AnyIO hiện có không làm thất bại bộ CP01.

Không chạy migration hay CLI đổi role vào DB thật. Không khởi động môi trường production, không công bố ứng dụng và chưa kiểm thử browser E2E/visual của giao diện mới.
