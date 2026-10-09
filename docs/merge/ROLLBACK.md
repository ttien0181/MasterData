# Migration rollback rehearsal — 2026-10-09

**Production rollback chưa được chứng nhận.** Schema rollback trên SQLite PASS;
rollback binary về `937bfa9` FAIL với export `local:` mới. Không thực thi thay đổi
trên production trong phiên kiểm định này.

## Bằng chứng đã chạy

| Kiểm tra | Kết quả | Phạm vi |
| --- | --- | --- |
| Fresh/partial upgrade tới `0025_pipeline_checkpoints` | PASS | Full backend baseline, SQLite riêng |
| Input migration 0021 giữ IDs, JSON, lineage; downgrade chặn external IDs giao nhau giữa nguồn | PASS | Input/migration tests trong baseline |
| 0022 generation downgrade/re-upgrade giữ serving, receipts và publication | PASS | `test_generation_migration_preservation_and_nondestructive_rollback` |
| 0023 giữ engine selection khi downgrade/re-upgrade | PASS | Adapter migration tests |
| 0024 giữ task/attempt history | PASS | `test_migration_preserves_jobs_and_attempt_history` |
| 0025 giữ durable checkpoints và dependencies | PASS | Checkpoint migration tests |
| Publish/replay snapshot 4.213 records, 5.281 pairs, 8 proposals, 5.273 review cases | PASS | SQLite riêng; generation batches 128 và 512 |
| Reader cũ đọc export mới có 4.205 output rows, `csv_bytes=NULL`, `local:` artifact | FAIL | Download và sample đều HTTP 500; DB mở read-only |
| PostgreSQL restore, khóa đồng thời, R2 và rollout binary/container thật | NOT_TESTED | Chưa có môi trường rehearsal được chỉ định |

Baseline evidence: `artifacts/merge-baseline/20261009T004216Z-pb91gsim/pytest.xml`.
Reader evidence: `artifacts/merge-validation/20261009-shadow-v2/rollback-reader.json`.
Snapshot publication/export evidence: cùng root, `db-internal-b128/metrics.json`
và `db-internal-b512/metrics.json`. PASS schema không đồng nghĩa PASS runtime cũ.

## Phương án ưu tiên: rollback lựa chọn engine, giữ runtime tương thích

1. Người phụ trách xác định incident, snapshot backup, run IDs đang chạy và
   phiên bản đang phục vụ. Chặn start/retry mới trong cửa sổ bảo trì; giữ khả năng
   đọc lịch sử. Chưa thực hiện bước vận hành này trên hệ thống thật.
2. Dừng admission và drain/cancel các worker của phiên bản cần rollback. Xác minh
   execution children đã dừng; không chỉ dừng API. Kiểm tra active attempts,
   leases và publication pointers trước khi cho worker khác nhận việc.
3. Giữ runtime có ownership fencing, generation gate và streaming export reader.
   Đặt `MDM_ENGINE_V2_ENABLED=false` và các opt-in decision/cluster/staging/
   streaming/resume flags về cấu hình đã được duyệt cho **run mới**. Đây là hướng
   dẫn, không phải cấu hình đã áp dụng trong phiên này.
4. Không sửa hoặc xóa `mdm_resolution_engine_selections`. Run đã pin v2 vẫn dùng
   profile đã lưu. Tắt global flag không đổi semantics của run đó. Run có
   publication history vẫn phải qua generation gate; không đưa nó sang writer cũ.
5. Kiểm tra serving counts, active generation, negative edges, reviewer decisions,
   revisions, golden overrides, membership/history, audit và export receipts so
   với backup. Đọc lại attachment, preview/compare và các immutable exports cũ.
6. Chỉ mở start cho run mới sau smoke auth/CSRF/ownership, review/golden,
   preview/export/download và worker queue. Retry protected runs chỉ qua runtime
   hiểu profile/generation/lease của chúng.

Các flags mặc định OFF trong source vẫn được giữ. Trạng thái flags thực tế của
production không được đọc hoặc thay đổi trong kiểm định này.

## Nếu bắt buộc quay về binary trước migration

**Không triển khai trực tiếp commit `937bfa9` lên dữ liệu đã migration.** Probe
thực tế cho thấy `_csv_response` và `_csv_sample` không đọc được export `local:`
với `csv_bytes=NULL`. Giữ hoặc backport reader hiện tại và mount artifact directory
đúng đường dẫn là điều kiện cần; tính tương thích của backport chưa được kiểm thử.
Không giải quyết bằng cách nạp toàn bộ CSV trở lại DB/RAM, xóa exports hay tạo
lại chúng với IDs khác.

Binary cũ cũng không thực thi ownership/generation/profile fences mới. Không cho
nó rerun hoặc publish các protected runs. Schema additive còn tồn tại không làm
writer cũ tự an toàn. Dừng toàn bộ external workers trước khi cân nhắc runtime
embedded-worker cũ. Chưa chứng nhận toàn bộ read/review routes của binary cũ đối
với state v2; giữ các routes tương thích ở runtime mới trong thời gian sửa incident.

## Schema downgrade và restore

Actual head là `0025_pipeline_checkpoints`. Các downgrade 0022–0025 cố ý giữ
private tables, receipts, engine selections, ownership/checkpoint history. Đây
là archival rollback, không phải xóa dữ liệu. Migration 0021 giữ nullable raw
metadata và chỉ khôi phục uniqueness `(run_id, external_id)` khi không có giao
IDs giữa source namespaces. Khi có collision, phải dừng downgrade; không đổi IDs,
gộp/xóa nguồn hoặc bỏ constraint để vượt qua kiểm tra.

Nếu cần restore backup, thực hiện trên DB clone, đối chiếu thêm artifact keys/
checksums và pointer state. Backup DB không đủ nếu thiếu local artifacts hoặc
objects R2 tương ứng. Không chạy orphan GC trong rollback; objects đang có lease,
receipts hoặc serving/export references phải được bảo toàn.

Các tests SQLite đã kiểm tra rollback/re-upgrade và preservation. Chưa chạy
PostgreSQL backup/PITR, contention, deployment rollback hoặc R2 object restore;
các bước này vẫn **NOT_TESTED**. RTO/RPO chưa có ngưỡng được chủ sở hữu duyệt.

## Tái tạo probe local

Sau khi tạo shadow snapshot và chạy materialization theo [SHADOW_DIFF](SHADOW_DIFF.md):

```powershell
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py materialize --directory artifacts/merge-validation/replay --dataset internal --batch 512
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py rollback-reader --directory artifacts/merge-validation/replay
```

Probe mở DB bằng SQLite URI `mode=ro`. Kết quả incompatibility trả exit 1 và
ghi `rollback-reader.json`; không thay serving/export state. Muốn chạy lại toàn
bộ rehearsal, dùng evidence directory mới, không xóa hoặc tái sử dụng state của
production. Release owner phải duyệt phương án rollback đã kiểm thử trước ON.
