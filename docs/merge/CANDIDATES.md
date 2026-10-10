# CandidateEngine thống nhất

Update 2026-10-08: opt-in `CandidateConfig(sparse_tfidf=True)` replaces the
partitioned TF-IDF pass with global record-IDF, sparse tiled record Top-K,
name/address config and workload admission. See [SPARSE_TFIDF](SPARSE_TFIDF.md)
for policy, tests, recall/RSS and rollback. The historical legacy description
below applies to `sparse_tfidf=False`; its dense similarity block was removed
(only one dense row remains for legacy argpartition ties).

SCORE update: full-snapshot frequency census, batched complete score artifacts
and shared ScoringEngine are now implemented; see [SCORING](SCORING.md).
Historical pending statements below describe the previous CandidateEngine task.

2026-10-08. P-119 parent `86900b0dd6be8b820f91d7c5a5fdb4a9031d4b41`.

`resolution_service._compute`, wrapper `blocking.generate_candidates` và
`StageRunner.build_candidates` dùng cùng `CandidateEngine`. Ngưỡng 10.000 vẫn
chọn orchestration/persistence, nhưng không còn chọn blocking policy khác.
Candidate index tích lũy toàn snapshot trước khi expand blocks; chunk chỉ là
đơn vị đọc. Không partition theo source hoặc chunk. ID phải là snapshot ID
không rỗng; duplicate ID gây lỗi, không overwrite hoặc ghép theo external_id.

## Chính sách và compatibility

Giữ chính sách P-119 đã có, không áp OneMaster quality/namespace semantics mới:

| Pass/source | Điều kiện | Max block mặc định |
| --- | --- | --- |
| exact_phone / exact_email | Mọi phần tử phones/emails có value và quality VALID, kể cả contact phụ | 20 |
| exact_gov_id | government_id không rỗng, _field_quality.government_id VALID | 20 |
| exact_name | name normalized không rỗng, không thêm quality gate | 60 |
| name_gram / address_gram | Trigrams với padding legacy; không bỏ pass recall | 15 |
| name_prefix_year | Name prefix 3 ký tự + DOB prefix 4 ký tự | 30 |
| surname_year | _name_parts.surname + DOB prefix 4 ký tự | 30 |
| name_province | Name + address_parts.province | 40 |
| phone_suffix_year | Phone primary VALID, suffix 4 ký tự + DOB prefix 4 ký tự | 30 |
| tfidf_name | TF-IDF legacy trên toàn snapshot, giữ partitions DOB/province và top-k/threshold | Theo TF-IDF legacy |

Không tự thêm scalar contact fallback, ID namespace gate, placeholder/name gate
hay date validation vào các default rules; đó là thay đổi policy riêng.
SHARED/INVALID/PLACEHOLDER/MISSING contacts và ID không tham gia exact blocks.
Existing normalized snapshot đã import được giữ nguyên ở StageRunner; raw-only
input chưa có normalized fields vẫn có normalization legacy trước BUILD.

`CandidateConfig` dùng chung cho engine, `_compute(candidate_config=...)` và
`StageRunner.run_all(candidate_config=...)`/`build_candidates(config=...)`.
CompositeRule/CompositeKey cấu hình field path, identity/prefix/suffix/year,
length, required VALID quality và max block. Giữ bốn composite mặc định.
Legacy `generate_candidates(records, max_bucket_size, top_k)` vẫn trả dict
pair -> set sources; max_bucket_size chỉ điều khiển exact_name như trước,
top_k vẫn là tham số compatibility không sử dụng.

Canonical pair là `(min(snapshot_id), max(snapshot_id))`. Union toàn cục hợp
set sources, bỏ self-pairs. Artifact rows và reasons có thứ tự ổn định.
`candidate_sources` được biểu diễn bằng set của engine, cột `reasons` trong
candidate Parquet và `blocking_reasons` trong serving contract; không đổi schema
hay pair ID SHA-256 hiện có. Mọi source từ TF-IDF được giữ, không lấy phần tử đầu.
TF-IDF nhận records theo ID tăng dần để tie selection độc lập input order/chunk;
ties của input legacy không theo thứ tự ID có thể chọn neighbors khác. Không đổi
TF-IDF implementation hoặc mở rộng thành mọi tied neighbors.

## Guard và telemetry

Trước khi combinations, block phải thỏa max_block_size toàn cục, giới hạn của
pass và max_pairs_per_block (mặc định 1.770 = C(60,2)). Không truncate block.
Giữ ngưỡng legacy; block bị chặn có reason max_block_size hoặc
max_pairs_per_block, potential/generated/skipped pair counts.
Global max_unique_pairs mặc định 50 triệu, configurable/None: vượt giới hạn
raise CandidateWorkloadExceededError kèm metrics, không trả partial candidates.
Đây là giới hạn mới fail rõ ràng, không âm thầm giảm recall.

Metrics phân biệt candidate_count (unique pairs), generated_observations
(pair emissions của từng block/pass), skipped_pair_observations (có overlap
giữa passes), duplicate_observations (cùng pair/source) và
union_collapsed_observations (mọi emissions đã gộp). Không gọi số emissions
bị skip là số unique pairs bị mất; pair có thể vẫn được pass khác tìm ra.
Excluded records thiếu value/VALID được đếm theo source; disabled passes được
ghi rõ. Thiếu sklearn gây ImportError rõ ràng thay vì bỏ TF-IDF âm thầm.

Engine ghi structured aggregate log không có PII. Stage BUILD metrics và
output candidate_metrics giữ cùng số đếm; cạnh Parquet có `.metrics.json` và
`.blocks.json`. Block keys chỉ lưu SHA-256. Không persist StageResult vào DB
hay thay serving schema trong task này.

## Phạm vi bằng chứng và giới hạn

Test-only frozen oracle `backend/tests/fixtures/legacy_candidate_reference.py`
lấy nguyên blocking.py tại parent P-119, không sửa assertions cũ. Tests so
default pairs **và sources** với oracle ở các ngưỡng và mixed snapshot, test
cross-chunk phone, 10.001 records, reversed/repeated multi-pass emissions,
deterministic shuffled batches, full StageRunner vs small `_compute`, guards,
quality exclusion và preservation imported normalized snapshot.

Index là global **in-memory**, chưa external disk index. Chưa chứng minh bounded
RSS/production throughput: blocks, unique pairs và TF-IDF result vẫn ở RAM;
TF-IDF vẫn dense similarity batches và partition legacy. Pair cap kiểm union,
không chặn RAM của TF-IDF trước khi helper trả kết quả. External index/spill và
TF-IDF memory redesign là task sau. SCORE frequency census/persistence khác
biệt giữa orchestration vẫn ngoài phạm vi; candidate equivalence không chứng
minh toàn bộ score/cluster equivalence cho mọi dataset.

## Tham chiếu OneMaster chỉ đọc

Đọc planner.py, union.py, exact.py, composite.py, blocking.py và configs/candidates.yaml
working tree; thiết kế lại trong P-119, không runtime import/copy source ngoài repo.
OneMaster chưa xác nhận LICENSE; không đưa source OneMaster vào distribution.

SHA-256 working-tree tại thời điểm đọc:

| src/onemaster/candidates file | SHA-256 |
| --- | --- |
| planner.py | 94BE1D121EB8FEA8AEA082A19803CA2D4EDD6466D4B98174E2E6125EC96748EC |
| union.py | 117A68AE0B0E60CA13EE5FE45C3FAFACF973A548F75C3A4261009C7E01FCC753 |
| exact.py | E485C7A3C24E860D1EDC5BAECE5C74AB718EE1BF97E6202F54C2F847BAD4E16E |
| composite.py | 6F06C409BDA4BFC6178868A1C1FC91700408B6B1628FA07E65967A8416C21BF6 |
| blocking.py | 0EF4458DD52EE9B558D618252D2E40DE766EFBF515C6C34FE668F674ED36E910 |
