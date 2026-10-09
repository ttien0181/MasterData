# Final migration validation — P-119 + OneMaster

Ngày kiểm định: **2026-10-09**. Kết luận readiness: **FAIL — chưa chuyển production**.
Không bật production flag, không migrate DB doanh nghiệp, không gọi R2/LLM thật.
Đây là báo cáo kiểm định, không phải phê duyệt release.

## Revision và phạm vi

Đã đọc toàn bộ `docs/merge`, lịch sử repository và các thay đổi migration từ
`937bfa9` tới `513cbae`. Commit đối chứng thật là
`937bfa9dc314cb4e64ee590ee05aae6aa5e618e4`; runtime được kiểm định là
`513cbaefbe68366db5c6546fa1b5dde421f70c4c`. `p119_current` tại runtime mới đã chứa
shared candidate/scoring; không được gọi nó là engine trước migration.

OneMaster HEAD `bc5fb6ea10d0c0771a96efff3fe9a5d806f0b1f5` có WIP. Kiểm định native
chạy trên bản sao source/config/tests/reference data, trong process và environment
riêng; không import vào runtime P-119. 359 files được pin SHA-256; tree manifest
digest `2565f9c57364c7a4ff06aa859944678249c2fa26d2c3cbbf9139f8decd311a15`.
Đối chiếu sau chạy xác nhận bản tham chiếu không đổi. Không coi HEAD sạch là
định danh đầy đủ của source OneMaster đang được kiểm định.

Python 3.11.9/Windows, Node 24.21.0, Next 16.3.6, Edge 154.0.4258.62.
Backend dùng SQLite riêng, DuckDB/Parquet, LocalObjectStore hoặc mocked storage.
OneMaster dùng interpreter có sẵn và bản sao riêng; external sockets bị chặn,
chỉ cho phép socketpair nội bộ của event loop. Frontend dependencies được cài
từ lockfile; source UI và lockfile không đổi.

## Kết quả thực thi

| Hạng mục | Kết quả | Bằng chứng / giới hạn |
| --- | --- | --- |
| Full backend/API suite | FAIL | 840 passed, 11 failed, 1 skipped; 390,11s |
| Selected P-119 import → pipeline → review/golden → preview/export/download scenarios với fixtures hợp lệ | PASS | Trong full baseline; legacy StageRunner E2E failures vẫn được ghi riêng bên dưới |
| Cùng normalized snapshot: old/current/v2 | PASS cho phần đã đo | Candidate sets/reasons, rule scores, decisions, membership và review cases không đổi trên hai snapshots; evidence/golden metadata có thay đổi chủ đích |
| Serial/stream, batch/partition/worker invariance | PASS cho các ca hoàn tất hợp lệ | Chỉ scoring config pin đổi theo batch; ba ca timeout không được coi PASS |
| Worker completion đảo ngược | PASS | Internal snapshot 4.213 records; NORMALIZE/SCORE/DECIDE partition 1 hoàn tất trước 0; baseline có thêm fixtures nhỏ |
| Small-partition completion budget | FAIL | Ba ca vượt 300s; tiny batch 17 dừng sau khoảng 359s, chưa có result hoàn tất |
| Matching quality trên ground truth | PASS việc tính metric; chưa đạt chứng nhận chất lượng | Có dữ liệu công khai thực và labels nội bộ; PERSON doanh nghiệp vẫn NOT_TESTED |
| Calibration của probability model | NOT_TESTED | Rule scores không phải xác suất; không có frozen calibrated/approved model phù hợp |
| Resource benchmark | PASS việc đo local | Runtime/RSS/CPU/disk/queue/wait/upload/stage/SQLite materialization; không có tuyên bố nhanh hơn |
| Kill/retry/cancel/resume và fencing | PASS local | Spawned process kill tại 7 boundaries, lease/timeout/cancel/replay/corruption tests trong baseline |
| Interrupted upload, partial publication, export coverage/overrides | PASS local | Fault injection/mock storage; real R2 NOT_TESTED |
| Schema migration/downgrade/re-upgrade preservation | PASS local | SQLite tới head 0025; xem ROLLBACK |
| Binary rollback về reader trước migration | FAIL | Export mới `local:` trả HTTP 500 ở reader cũ |
| TypeScript / Vitest / Next build | PASS | 50 tests; production build thành công |
| Browser E2E | PASS | 10 tests dev + 10 tests production-build CLI; API routes mocked |
| Actual frontend `api.ts` → authenticated backend smoke | PASS | Baseline bridge disposable TestClient; không thay bằng mocked TypeScript consumer |
| Toàn bộ live browser/API/worker/PostgreSQL/R2 stack | NOT_TESTED | Browser smoke và backend tests không phải live full-stack deployment |
| Native OneMaster selected suites | FAIL | 120 passed, 6 failed, 108 warnings; 395,25s; không chứng nhận toàn bộ OneMaster |
| Responsible production approval | NOT_TESTED | Chưa có người/phạm vi/ngưỡng được duyệt |

## Baseline và lỗi chưa xử lý

Full baseline report: `artifacts/merge-baseline/20261009T004216Z-pb91gsim/`.
11 failure IDs giống baseline `20261009T002427Z-uoxjv49f` trước export migration:
một CORS local Next origin expectation trong `APP_ENV=test`; mười StageRunner
fixtures thiếu `external_id` tại projection CLUSTER. Có một TF-IDF partition skip.
Không sửa assertion hoặc suppress lỗi để làm xanh audit. Full backend vẫn FAIL.
Validation harness có thêm 6 tests PASS trong evidence
`artifacts/merge-validation/harness-tests-final/`.

OneMaster report cuối: `artifacts/merge-validation/om4/`. Sáu failures:

- Empty-candidate schema và empty-score serial/stream (3 cases): model bootstrap
  đi vào training đòi ít nhất hai records; diagnostic `ValueError` tại
  `matching/baseline.py:30`, thay vì hoàn tất artifact rỗng.
- `test_real_runner_flag_equivalence[1]`: model-version/audit khác nhau.
- `test_real_runner_flag_equivalence[2]`: review payload khác nhau.
- `test_serving_table_equivalence_false_true[2]`: review artifact chứa match weight
  khác nhau, gồm `-10.928739126694575` và `-18.346591641580474`.

Các khác biệt native này **không được phân loại thành thay đổi chủ đích đã chấp
thuận**. Cần điều tra model pin/training và replay trong OneMaster; không tự
chuẩn hóa bỏ score/model diff. Những lần chạy sơ bộ lỗi guard socketpair,
reference data thiếu và đường dẫn Windows dài được lưu riêng, không tính thành
lỗi engine ở kết quả cuối.

## Chất lượng matching

Ground truth công khai: QCRI Fodors/Zagats, revision pin, 864 restaurant records,
112 gold links; đo cross-source closed-world theo perfectMapping. Đây là domain
stress với `UNKNOWN`, không phải PERSON hoặc chứng nhận PERSON cho nhà hàng.
PERSON sample nội bộ có 4.213 records, 1.200 labels (960 positive/240 negative),
do nhóm tạo; chưa được xác nhận bằng annotation doanh nghiệp độc lập.

| Snapshot / mọi profile đã hoàn tất | Candidate recall | TP / FP / FN / TN tự gộp | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| PERSON sample nội bộ | 944/960 = 98,3333% | 2 / 0 / 958 / 240 | 100% trên 2 predictions có nhãn | 0,2083% | 0,4158% |
| Public restaurant cross-source | 112/112 = 100% | 0 / 0 / 112 / 176.311 | Không xác định: không auto-merge | 0% | 0% |

Nội bộ có 8 predicted auto pairs, trong đó 6 không có nhãn; không tính chúng
thành true/false positives. 918 labeled positives được đưa vào REVIEW; 24 còn
trong candidates nhưng không có serving pair, 16 không được blocking tìm thấy.
Observed false-positive merges và clusters vi phạm known negatives đều bằng 0;
đây không phải chứng minh false cluster merge rate bằng 0 trên toàn bộ entities.
Không có complete enterprise entity truth. Precision 2/2 không đủ chứng nhận
99,5%; Wilson 95% lower bound chỉ khoảng 34,24%.

Không dùng labels để điều chỉnh policy/threshold/model. Không thay rule units
bằng probability, không bật sparse TF-IDF/Splink theo engine-v2 switch. Chưa có
bằng chứng v2 cải thiện precision/recall/F1 hoặc hiệu năng trên các snapshots này.

## Acceptance và quyết định release

Các ngưỡng dưới đây là **đề xuất chưa được duyệt**, không thay thế tiêu chí của
người phụ trách. Bật engine chỉ sau khi owner chốt tiêu chí, mọi gate bắt buộc
PASS và ký approval gắn với code/config/model/input hashes.

| Gate đề xuất | Hiện trạng |
| --- | --- |
| Không còn required test failures hoặc waiver được owner ký | FAIL: backend 11, OneMaster 6 |
| Invariance trên toàn bộ legal budget matrix đã chốt | Chỉ các ca hoàn tất PASS; timeout cases chưa có kết quả equivalence |
| Representative PERSON holdout; candidate recall ≥99%; auto recall không thấp hơn baseline | NOT_TESTED enterprise; sample candidate recall 98,33% dưới ngưỡng đề xuất |
| Auto precision Wilson 95% lower bound ≥99,5%; complete entity labels và false-cluster bounds được duyệt | NOT_TESTED / không đủ labels |
| Frozen calibrated model nếu dùng probability; không training theo live batch | NOT_TESTED; rule mode không cần giả lập calibration |
| Runtime/RSS/disk/queue/upload/DB budgets và load percentile đạt SLA được duyệt | NOT_TESTED production; local có timeout và RSS/overhead tăng |
| PostgreSQL/R2 kill/retry/cancel/restore + rollback/download rehearsal | NOT_TESTED live; old export reader FAIL |
| Người chịu trách nhiệm duyệt release và rollback | NOT_TESTED / chưa có approval |

Đường runtime và flags giữ nguyên. Cần xử lý các blockers, thu thập PERSON
ground truth có provenance, chạy isolated PostgreSQL/R2 + live full-stack,
đóng acceptance criteria và rollback rehearsal trước khi cân nhắc ON.

Chi tiết và cách tái tạo: [SHADOW_DIFF](SHADOW_DIFF.md),
[BENCHMARK](BENCHMARK.md), [ROLLBACK](ROLLBACK.md).
