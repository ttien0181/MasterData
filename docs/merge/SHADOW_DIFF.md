# Reproducible shadow comparison — 2026-10-09

**Kết luận:** các ca P-119 đã hoàn tất trên legal configurations giữ nguyên
candidate coverage, numeric rule scores, pair decisions, cluster membership và
review cases. V2 thêm evidence/golden provenance metadata có chủ đích. Native
OneMaster còn failures; không chứng nhận migration hoặc production readiness.

## Pin engine và input

| Đối chứng | Định danh / cấu hình |
| --- | --- |
| Engine trước migration | `937bfa9dc314cb4e64ee590ee05aae6aa5e618e4`, source lấy bằng `git archive`, process riêng |
| Runtime hiện tại | `513cbaefbe68366db5c6546fa1b5dde421f70c4c` |
| `current` shadow | Shared candidate/scoring mới; decision/cluster/staging v2 OFF |
| `v2` shadow | Decision/cluster/staging v2 ON trong execution context riêng; rule mode; sparse TF-IDF/resume OFF |
| `v2-stream` shadow | Cùng v2 + năm boundary flags ON; LocalObjectStore riêng |
| OneMaster native reference | HEAD `bc5fb6e…` + dirty source manifest; không dùng chung module namespace với P-119 |

P-119 runtime manifest: 119 files, SHA-256 của sorted path→hash map
`1053b930dbce9282f8963f3d2d15361a5c80bd691bd984593867e736b4c5de44`.
Full manifests, dependencies và raw outputs được lưu ở ignored evidence directories.
Không commit PII, credentials, normalized record values hoặc downloaded datasets.

Inputs dùng cùng normalized snapshot tạo bởi P-119 standardization, fixed IDs/
source systems, fixed run ID `shadow-fixed-run`, fixed policy identity. So sánh
này cô lập matching/pipeline transport; không phải import-normalization shadow
toàn bộ raw UI upload giữa hai phiên bản. Import/lineage contract được kiểm định
riêng bởi baseline tests. Entity/noise/difficulty/labels không có trong raw_fields
hay normalized_fields đưa vào matching. Có validation duplicate IDs, label IDs
không tồn tại và conflicting labels. Không tune theo holdout.

## Dữ liệu có nhãn

Public source: [QCRI deeper-lite Fodors/Zagats](https://github.com/qcri/deeper-lite/tree/c39fcae0f59d79f6842da3c6cb07c2a87bf1b55b/Lua/data/fodors-zagats).
Revision `c39fcae0f59d79f6842da3c6cb07c2a87bf1b55b`: Fodors 533 rows, Zagats
331 rows, perfectMapping 112 distinct links. Số này lấy từ đúng source pin,
không thay bằng một con số phổ biến của phiên bản dataset khác.

| Source file | SHA-256 bytes |
| --- | --- |
| fodors.csv | `596476f1684c8cc89ed386cd13538fdad47697c93d5d16672cf0c3f38e012eec` |
| zagats.csv | `2fb49939643afa0559ec22f2fcf98e659023cbbbb5f45fdb4e26d54321b69d7d` |
| perfectMapping | `b336a18c8295e7b69deb709bae7d9cdb4828467fcf25a204b8011dd9496a5db7` |
| Internal records.csv, LF canonical | `16587b35766a107c4deb1ff4e237f122a954b9b27f53c15ff12fcc71ec83b046` |
| Internal pairs.csv, LF canonical | `4272fe4be8e06cebcf18abf6f1bd88f63de0dc854d83175a155d465c8f1746be` |

Windows checkout raw hashes của internal records/pairs lần lượt là
`955ab23b71876ba4ed4ee844a6feb2f90c133fa253575125fb346b3b206d3bf7` và
`50982c914c097a8410ed1348641d2c085bbbbe3da95bac1a4e718de46d057e8b`.
Khác LF canonical do CRLF, đã kiểm tra trực tiếp; không phải input values khác.

Normalized snapshot hashes: public
`b5d79a92ff580989e82efdcc414e417c79a0d7bb88417d06f24670cff0638ca6`;
internal và labels hashes có trong `provenance.json`. Cả source bytes và normalized
snapshot/label projections đều được lưu để phát hiện input drift.

Public labels được đánh giá theo **closed-world cross-table perfectMapping**:
176.423 cross-source pairs = 112 positive + 176.311 negative. Không gán negative
cho within-source pairs. Restaurants dùng `UNKNOWN` compatibility stress cùng
VN normalization; không gán giả PERSON/không coi policy người phù hợp cho doanh
nghiệp. Dataset có gold links thực nhưng ngoài miền PERSON production.

Internal `masterone_test/records.csv` có 4.213 records, labels trong `pairs.csv`
có 1.200 pairs (960 positive/240 negative). Đây là team-generated PERSON sample,
không phải independently annotated enterprise holdout. Chỉ 1.200 pairs này được
scoring quality; mọi pair khác UNKNOWN. Không dùng generator `entity_id` để tự
nhận complete gold clusters. Cần thêm PERSON truth có provenance trước rollout.

## Observed old → current → v2 diff

| Phần so sánh | Public old→current | Internal old→current | Current→v2 |
| --- | --- | --- | --- |
| Candidate count | 27.645→27.645 | 32.202→32.202 | Không đổi |
| Candidate added/removed/reason changes | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Numeric rule-score changes trên common candidates | 0 | 0 | 0 |
| Serving pairs added/removed | 0 / 0 | 0 / 0 | 0 / 0 |
| Decision/group/subgroup/triage changes | 0 | 0 | 0 |
| Pair evidence changes | 0 | 0 | 122 public / 5.281 internal |
| Membership added/removed | 0 / 0 | 0 / 0 | 0 / 0 |
| Golden payload changes, common memberships | 0 | 0 | 0 public / 8 internal |
| Selected golden value changes | 0 | 0 | 0 |
| Review case payload changes | 0 | 0 | 0 |

Old/current pair payload hashes khác nhau vì current thêm `rule_score`,
`scoring_model_version`, `scoring_model_hash`, `scoring_config_hash` cho 122/5.281
serving pairs. Alias `rule_score` giữ đúng giá trị `score`; không đổi sang
probability. Current/v2 evidence thêm `verified_both` và conflict metadata ở
những field tương ứng. Tám internal golden payloads thêm survivorship version,
provenance/all-source/alternative metadata ở cả bốn fields; selected values giữ
nguyên. Đây là actual diffs được giải thích, không tuyên bố raw JSON identical.

## Batch, partition và completion order

Có 14 completed cases trong legal settings: old/current của mỗi dataset;
v2 serial batches 17/512; public stream b512,p512,w2, b4096,p1024,w2,
b4096,p2048,w1; internal stream b1024,p1024,w2 reverse và b4096,p2048,w1/w2.
Tám comparisons từ v2 serial b17 tới các legal v2 alternatives PASS cho business
payload sau khi loại **duy nhất** `scoring_config_hash`. Hash này bắt buộc đổi
khi scoring batch config đổi; harness vẫn báo strict full-payload hash khác và
liệt kê field, không bỏ qua decision/evidence/golden hoặc tùy tiện normalize floats.

Với scoring batch 4096 cố định, complete payload hashes thực sự identical:

- Public p1024,w2 và p2048,w1:
  `6bd52afd3d60c62578b7db93c86b356f5d06f2c8778fc8fb0cb232fb606f57e4`.
- Internal p2048,w1 và w2:
  `3583fdd1aa15ddc60f7d549ab7f076db22630d7661b62908e2f6210ec5b35f27`.

Internal reverse case ghi actual completion sequence: normalized partitions
1,2,3,0,4; scores 1,0,2,4,5; decision prep 1,0,2,4,5. First partition đợi
second completion ở cả ba boundaries. Mọi business fields vẫn giống serial.

Public b128,p128,w1; public b256,p256,w2 reverse; internal b512,p256,w1 timeout
300s: **FAIL completion probe / NOT_TESTED equivalence**. Tiny b17,p128 bị dừng
khi chưa xong. Một p4096 exploratory case vượt Settings limit 2048 bị loại khỏi
acceptance; harness hiện chặn cấu hình đó. Không gọi các incomplete cases PASS.

## Thay đổi chủ đích trong migration, kể cả chưa kích hoạt bởi snapshots này

| Commit | Thay đổi và giới hạn kết luận |
| --- | --- |
| `7d18a29`, `86900b0` | Lossless mapping/lineage, source-scoped identity; không phục hồi giả metadata lịch sử hoặc leak labels vào engine |
| `7f91458` | CandidateEngine toàn snapshot; sửa khác biệt coverage của legacy large path (exact-name/TF-IDF thiếu các passes). Shadow dưới 10k không chứng minh equivalence của old large path |
| `b4dea98` | Opt-in sparse global IDF/Top-K có deterministic ties và workload caps; khác legacy partitioned TF-IDF theo thiết kế. Labeled sparse shadow NOT_TESTED, không tự ON |
| `463afac` | Shared full-snapshot frequency census và all-candidate typed SCORE artifact; sửa candidate-only frequency của large path; rule units giữ nguyên |
| `f3d6aea` | PERSON evaluator tách evidence/constraints/eligibility/review; khác contact không là identity negative; ID verification/type/namespace; REJECTED không tự tạo cannot-link; ORGANIZATION không dùng person semantics |
| `5717d9b` | Canonical edge order theo weight/record IDs, all-member guards, bridge/cannot-link caps; same-source được phép theo policy; golden provenance/alternatives/aware timestamps, overrides được giữ |
| `b547036`, `a2427e3` | Durable staging/atomic publication; insert-only preservation; API DTO adapter, persisted profile selection và golden override precedence |
| `0069462`, `696a6f3` | Independent durable worker/lease fencing; bounded reference/upload queues, immutable partition/readback receipts; không tuyên bố global matching state bounded |
| `aaa4896`, `f810d9e` | Controlled overlap có global barriers; pin code/input/config/model/dependencies và resume; completion order không được quyết định business unions |
| `513cbae` | Streaming CSV/optional Parquet, disk coverage indexes, complete upload/readback trước publication; local artifact reader mới, idempotent retry và resource metrics |

Các semantics changes không được gọi là “tốt hơn” nếu chưa có số đo phù hợp.
Review/golden overrides và conflict preservation được kiểm định bằng baseline
cases riêng; hai labeled snapshots không chứa mọi verified-ID/override scenario.

## Quality protocol và native reference failures

Positive prediction là pair có `HIGH_CONFIDENCE_PROPOSAL` sau cluster guards.
Candidate recall mẫu số là mọi labeled positives, kể cả bị blocking bỏ sót.
Precision chỉ trên predicted pairs có nhãn; FN gồm positives không auto-merged,
kể cả REVIEW/insufficient/missing candidates. Do đó đây là auto-merge operating
point, không phải độ chính xác “review đáng để người xem”. `MERGEABLE` và accepted
auto edges trùng nhau ở hai snapshots này. Complete entity precision/false-cluster
rate không được suy từ incomplete labels; chỉ báo clusters vi phạm known negatives.

Internal mọi completed profiles: recall 944/960; TP=2, FP=0, FN=958, TN=240;
auto predicted 8, unknown 6; P=1 trên 2 labeled predictions, R=0,00208333,
F1=0,00415800. Public: recall 112/112, không auto prediction, precision undefined,
R/F1=0. Không có evaluated match probabilities; calibration **NOT_TESTED**.
Chi tiết chất lượng và acceptance: [FINAL_REPORT](FINAL_REPORT.md).

Native OneMaster selected suites: **120 PASS / 6 FAIL**. Empty model bootstrap
và model/audit/review-score parity failures được ghi ở FINAL_REPORT; không đổi
assertions hay coi score changes thành intentional metadata. Fixtures native có
training/path verification, không phải frozen calibrated production models.
Chưa có direct labeled comparison giữa toàn bộ OneMaster engine và P-119 trên
cùng raw input/normalization/model contract: **NOT_TESTED**. Rule-score của P-119
không thể so numeric với Splink probabilities bằng cách cùng đặt tên `score`.

## Commands tái tạo

Chạy từ P-119, dùng directory mới. Scripts tự cô lập DB/R2/LLM/tracing, không
đọc/ghi production state. Root bên dưới chỉ là local evidence.

```powershell
.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py --suite all --timeout 900
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py prepare --directory artifacts/merge-validation/replay --reference "C:/VinUni/Dự án nhóm/onemaster-workspace"
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py matrix --directory artifacts/merge-validation/replay --timeout 300
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py child --directory artifacts/merge-validation/replay --dataset public --profile v2-stream --batch 4096 --partition 1024 --workers 2
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py child --directory artifacts/merge-validation/replay --dataset public --profile v2-stream --batch 4096 --partition 2048 --workers 1
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py child --directory artifacts/merge-validation/replay --dataset internal --profile v2-stream --batch 4096 --partition 2048 --workers 1
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py summarize --directory artifacts/merge-validation/replay
.venv/Scripts/python.exe -X utf8 scripts/merge_shadow_validation.py materialize --directory artifacts/merge-validation/replay --dataset internal --batch 512
.venv/Scripts/python.exe -X utf8 scripts/run_onemaster_validation.py --reference "C:/VinUni/Dự án nhóm/onemaster-workspace" --python "C:/VinUni/Dự án nhóm/onemaster-workspace/myenv/Scripts/python.exe" --directory artifacts/merge-validation/onemaster-replay
```

`matrix` trả nonzero khi có failed/timeout case. Complete evidence được reuse
chỉ khi rerun cùng directory; không tính nó thành trial mới. Dùng root mới cho
failed attempts. Native reference replay cần khớp source manifest, interpreter/
dependency versions và reference data; HEAD riêng không đủ tái tạo dirty tree.
Public source URLs đã pin; internal LF hashes/normalized projections phải khớp.
Giữ evidence snapshot nếu workspace OneMaster thay đổi sau audit.

Frontend: `npm ci --ignore-scripts --no-audit --no-fund`, `npm run lint`,
`npm test -- --reporter=dot`, `npm run build` trong frontend, với telemetry OFF
và `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:9` khi build. Từ root chạy
`node scripts/run_merge_frontend_smoke.mjs` và thêm `--production` cho compiled
Next CLI smoke. Helper dùng fresh port, không reuse developer server. Browser
specs mock API; standalone container/live cookie/CORS/PostgreSQL/R2 vẫn NOT_TESTED.
