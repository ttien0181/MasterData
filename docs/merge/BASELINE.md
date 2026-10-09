# Baseline trước migration thuật toán

Ngày: 2026-10-08. P-119 parent `937bfa9dc314cb4e64ee590ee05aae6aa5e618e4`. Chỉ bổ sung docs, test characterization và runner; không thay runtime, schema, policy, thuật toán hay frontend.

## Môi trường và cách chạy an toàn

Venv cũ `venv/Scripts/python.exe`: Python 3.11.9; có pytest/FastAPI/httpx nhưng thiếu SQLAlchemy, PyArrow, DuckDB, Splink, sklearn, masterone. Không sửa venv cũ.

Tạo `.venv` riêng (ignored). Editable install `pip install -e './backend[dev]'` thất bại do setuptools editable wheel encode đường dẫn tiếng Việt bằng cp1252, kể cả thử PYTHONUTF8. Đây là lỗi setup đã thực thi, không phải failure thuật toán. Dùng non-editable install để lấy dependency; runner đặt PYTHONPATH vào source backend hiện tại, không test bản wheel cũ.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install './backend[dev]'
.\.venv\Scripts\python.exe -X utf8 scripts/run_merge_baseline.py --suite contracts
.\.venv\Scripts\python.exe -X utf8 scripts/run_merge_baseline.py --suite focused
.\.venv\Scripts\python.exe -X utf8 scripts/run_merge_baseline.py --suite all
```

Runner override APP_ENV=test, SQLite tạm, empty R2/Gemini/OpenRouter, LLM/semantic/tracing disabled, AWS metadata disabled; không mở live app lifespan. Plugins pytest được giới hạn pytest_asyncio và `tests.merge_baseline_plugin`. Plugin opt-in chuyển StageRunner default scratch vào tmp_path từng test, tôn trọng output_dir explicit; không thay class production hoặc sửa fixture/test cũ. Test service integrations cần mock/fixture riêng. Không coi runner là network firewall hay sandbox: không thêm live-service test vào suite nếu chưa audit.

Reports dưới `artifacts/merge-baseline/<UTC>-<suffix>/`: pytest.log, pytest.xml, dependencies.txt, run.json. Không commit generated reports/datasets. Mỗi run có thư mục mới, không overwrite baseline trước.

## Kết quả thực thi

| Run / command | Kết quả thật | Report directory sau `artifacts/merge-baseline/` |
| --- | --- | --- |
| Exploratory `--suite focused` trước scratch plugin | 316 passed, 12 failed, 1 skipped; 34.47s; exit 1 | `20261008T091919Z-_jfa265q` |
| Exploratory `--suite all` trước scratch plugin | 403 passed, 12 failed, 1 skipped; 32.60s; exit 1 | `20261008T092023Z-ceqdpqrj` |
| `--suite all` với scratch plugin và 18 test mới | 404 passed, 12 failed, 1 skipped; 33.61s; exit 1 | `20261008T092142Z-d88dko0l` |
| **Final `--suite contracts`** (19 test mới) | **19 passed; 2.52s; exit 0** | `20261008T092306Z-kkiicoyz` |
| **Final `--suite all`** (418 test cases) | **405 passed, 12 failed, 1 skipped; 32.74s; exit 1** | `20261008T092320Z-532w849v` |
| `python -m ruff check scripts/run_merge_baseline.py backend/tests/merge_baseline_plugin.py backend/tests/test_mdm/test_merge_baseline_contracts.py` | All checks passed, exit 0 (sau sửa import/style chỉ file mới) | Tool output |
| `python -m pip check` trong `.venv` | No broken requirements found, exit 0 | Tool output |
| `npm.cmd test -- --reporter=dot`, cwd frontend | BLOCKED/exit 1: `vitest` not recognized; dependency không có sẵn; không npm install hay sửa frontend | Tool output |

Không có collection error trong backend final. **Suite tổng không PASS.** Không sửa algorithm/runtime, không skip/xfail hoặc đổi assertion test cũ để làm xanh.

### Failure inventory của lượt final

| Số case | Cases | Evidence và giới hạn kết luận |
| --- | --- | --- |
| 9 | `test_remaining_tasks::TestParquetArtifacts::test_stage_runner_write_parquet`; `test_stage_runner` keys/results/duration/twin, E2E same-name/negative/different, stage-results memory test | BinderException: fixture in-memory thiếu `source_system` mà SCORE SELECT yêu cầu. Không suy ra DB-backed path cũng thiếu cột |
| 1 | `test_stage_runner::TestBoundedMemory::test_1000_records_completes` | BinderException thiếu `external_id` ở CLUSTER, fixture không theo complete DB projection |
| 1 | `test_stage_runner::TestStageRunner::test_empty_records_no_crash` | CatalogException: empty NORMALIZE không tạo `norm1` nhưng CLUSTER đọc bảng đó; code-path issue chưa sửa |
| 1 | `test_auth::test_local_next_dev_origin_can_register` | Preflight 400 vs expected 200. Test giả định development regex nhưng runner cố ý APP_ENV=test; CORS middleware cấu hình tại app import. Không kết luận production CORS lỗi |
| 1 skipped | `test_partition_keys_two_branches` | Existing skip: internal `_partition_keys` removed in Big Data refactor. Không tính thành PASS |

Lượt exploratory còn thấy Access denied khi test dùng chung run1/default DuckDB file. Scratch plugin loại bỏ cross-test file sharing; test duration nay bộc lộ cùng lỗi fixture source_system. Exploratory tests đã dùng legacy `C:\tmp\mdm_stages` ngoài repo; không xóa thư mục đó vì không có inventory/quyền sở hữu các file có sẵn. Final runner chỉ redirect default scratch vào artifact-owned temp trong P-119.

Smoke mới dùng đủ `id,external_id,source_system,entity_type,raw_fields,normalized_fields,quality_flags`, output_dir riêng và đóng DuckDB connection; đi qua 7 stage, có 1 candidate/1 scored pair. Đây là local smoke hai record, không kiểm chứng PostgreSQL INSERT, 10k+ performance hay streaming.

### Dependency versions đã chạy

Python 3.11.9 / Windows; pytest 9.1.1, pytest-asyncio 1.4.0, FastAPI 0.142.4, Starlette 1.7.0, Pydantic 2.13.5, SQLAlchemy 2.1.4, Alembic 1.20.0, DuckDB 1.5.6, PyArrow 25.0.1, Splink 5.0.0, scikit-learn 1.9.1, pandas 3.0.6, httpx 0.28.1. Full transitive freeze được ghi cho từng report; đây là môi trường baseline riêng giải từ ranges hiện tại, **không khẳng định cùng môi trường production**.

## Test mới và phạm vi bằng chứng

`backend/tests/test_mdm/test_merge_baseline_contracts.py` bảo vệ registered routes, active request DTO khác legacy DTO, counter/task keys, storage fields human-owned và uniqueness record hiện tại; kiểm environment runner không kế thừa live DB/R2, scratch isolation và smoke complete records. Không chứng minh toàn bộ response schema hay replay correctness production.

Existing MDM tests cung cấp characterisation workflow/import/review/golden/preview/export/split/RBAC/audit; StageRunner tests dùng dữ liệu nhỏ/in-memory input. Test mang tên bounded-memory hiện chủ yếu đếm StageResult, không đo RSS. Nhánh >10.000 PostgreSQL materialization, concurrent workers, R2/crash/recovery cần suite riêng sau khi được giao.

## Chưa kiểm định

- OneMaster tests không chạy; repository chỉ đọc.
- PostgreSQL/Neon thật, R2 thật, LLM thật và full frontend/browser E2E không dùng cho baseline này.
- Không chạy benchmark 21k/1M hoặc migration thuật toán; kết quả nghiên cứu cũ không được coi là baseline luồng API hiện tại.
- Dependency ranges chưa lock; recorded freeze mới là môi trường lần chạy. Không nâng dependency production để làm test xanh ngầm.
