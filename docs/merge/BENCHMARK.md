# Migration resource benchmark — 2026-10-09

**Local diagnostic measurements only. Production performance: NOT_TESTED.**
Các số đo không chứng minh engine mới nhanh hơn hoặc dùng ít RAM hơn. Có các
ca streaming chậm hơn đáng kể và ba ca vượt completion budget.

## Phương pháp và giới hạn

Evidence root: `artifacts/merge-validation/20261009-shadow-v2/`. Mỗi compute case
chạy trong Python process mới, cùng normalized snapshot và rule policy; không
đưa labels vào engine. Runtime/CPU đo riêng `_compute`, không gồm chuẩn bị dataset,
dependency installation, report serialization, supplemental legacy SCORE probe
hoặc DB publication. DB/export được đo riêng bên dưới.

Peak RSS dùng Windows `PeakWorkingSetSize`, gồm imports trong cả lifetime process,
không phải RSS delta hoặc riêng Python allocations. CPU là process CPU seconds,
gồm các threads, có thể lớn hơn wall time. Disk là sampled peak tổng workspace
compute và **simulated remote store trên cùng ổ đĩa**, không phải riêng disk của
worker khi R2 thật ở xa. Không suy diễn filesystem bytes thành disk IOPS.

Queue peak là sampled in-flight depth, không phải số pairs. Wait được đo tại
Condition admission waits của consumer/uploader; tổng các thread waits có thể
chồng nhau. Upload là tổng thời gian các lần local file/manifest upload, không
phải network wall time hoặc latency R2. Export upload metric gồm remote readback.
Không có đo throughput R2/PostgreSQL, network interruption thật hoặc OS enforced
memory cap. Stage spans có overlap, không cộng thành một pipeline waterfall.

Đây là một lần đo mỗi cấu hình trên máy đang dùng chung. Một số checks/browser/
native tests chạy đồng thời; không có CPU/disk isolation, repeated trials hoặc
p50/p95. Sampler ban đầu 20ms, các ca sau 500ms; overhead sampler cũng nằm trong
compute time. Không dùng tỷ lệ giữa các dòng để khẳng định lợi ích nhân quả.

## Compute cases đã hoàn tất

MiB = 1.048.576 bytes. `b` = scoring batch; `p` = partition rows; `w` = consumer/
upload workers. Old/current chạy serial; `p/w` chỉ có tác dụng với stream.
Queue của mọi stream case trong bảng đạt sampled peak 4; serial bằng 0.

| Dataset / engine / b,p,w | Wall s | CPU s | Peak RSS MiB | Disk peak MiB | Local upload sum s | Admission wait sum s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Internal old, b256 | 3.570 | 3.297 | 237.66 | 0.00 | 0 | 0 |
| Internal current, b256 | 9.311 | 9.266 | 338.09 | 4.62 | 0 | 0 |
| Internal v2 serial, b17 | 21.492 | 19.578 | 309.59 | 43.71 | 0 | 0 |
| Internal v2 serial, b512 | 10.153 | 9.656 | 296.64 | 24.28 | 0 | 0 |
| Internal v2 stream, b1024,p1024,w2, reverse | 93.062 | 96.047 | 443.92 | 30.31 | 4.777 | 60.038 |
| Internal v2 stream, b4096,p2048,w1 | 34.764 | 34.641 | 508.43 | 29.85 | 2.283 | 7.078 |
| Internal v2 stream, b4096,p2048,w2 | 35.163 | 35.797 | 514.34 | 29.85 | 2.489 | 16.663 |
| Public old, b256 | 2.400 | 2.312 | 195.71 | 0.00 | 0 | 0 |
| Public current, b256 | 6.716 | 6.391 | 256.79 | 2.83 | 0 | 0 |
| Public v2 serial, b17 | 14.354 | 13.813 | 264.82 | 18.64 | 0 | 0 |
| Public v2 serial, b512 | 7.221 | 7.188 | 264.45 | 2.79 | 0 | 0 |
| Public v2 stream, b512,p512,w2 | 204.450 | 214.922 | 304.67 | 6.98 | 3.931 | 125.939 |
| Public v2 stream, b4096,p1024,w2 | 47.121 | 49.313 | 378.50 | 5.84 | 4.003 | 35.745 |
| Public v2 stream, b4096,p2048,w1 | 25.567 | 26.125 | 397.81 | 5.71 | 2.168 | 5.493 |

Old `_compute` ghi 0 temporary score bytes; nó không có complete typed SCORE
artifact/coverage work của đường mới. Các đường không thực hiện cùng lượng I/O,
nên đây không phải benchmark cho hai implementations có mọi thuộc tính giống nhau.
V2 streaming vẫn giữ global records/candidate indexes/cluster state trong RAM.
Không tuyên bố toàn bộ worker bounded memory hoặc O(batch).

Một probe public `p4096` hoàn tất trong 18,484s, RSS 482,89 MiB, nhưng được chạy
trước khi harness chặn partition vượt schema limit 2048. **Loại khỏi acceptance**;
không dùng probe ngoài validated config làm bằng chứng production. Artifact được
giữ, không đổi tên thành một cấu hình hợp lệ.

## Ca chưa hoàn tất và resource budgets

| Case | Outcome | Kết luận |
| --- | --- | --- |
| Public stream b17,p128,w1 | NOT_COMPLETED | Dừng khoảng 359s; mới 106/1.627 score partitions, 1.802/27.645 rows |
| Public stream b128,p128,w1 | FAIL completion probe | Timeout 300s; equivalence NOT_TESTED |
| Public stream b256,p256,w2, reverse | FAIL completion probe | Timeout 300s; equivalence NOT_TESTED |
| Internal stream b512,p256,w1 | FAIL completion probe | Timeout 300s; equivalence NOT_TESTED |

Compute-only attempts không có serving publication. Không coi partial Parquet,
uploaded partition hay thiếu `result.json` là output hoàn tất. Tiny-batch files
được giữ để điều tra, không cleanup trong lúc test rồi tự nhận PASS.

Budgets của các ca lớn hợp lệ: partition 16 MiB, queue 64 MiB/4 partitions,
consumer/upload workers 1 hoặc 2, disk 2 GiB, admission timeout 60s. Scoring
batch 17/512/1024/4096; legal partition 512/1024/2048. Các probes ban đầu dùng
partition 2 MiB/queue 8 MiB với các caps về số files giữ nguyên. Harness completion
budget 300s là budget kiểm định local, **không phải production SLA đã duyệt**.
Settings production defaults/flags không bị thay đổi.

Nhiều small partitions tăng manifest/lease/disk-guard/serialization/upload work;
sampler cũng có overhead. Chưa tách từng nguyên nhân bằng profiler hoặc chứng
minh bottleneck đơn nhất. Phải đo lại trên host riêng, workload lớn có provenance,
fixed budgets, repeated trials và đồng thời concurrent-worker pressure trước ON.

## DB materialization và export

Đã migrate DB SQLite mới tới head, seed snapshot theo batches, stage/seal,
kiểm tra serving rỗng trước promote, validate/promote và replay. Batches 512
còn kiểm tra mọi serving pair IDs/endpoints/score/decision/DTO evidence đối với
computed payload. DB receipts dùng DB bytes; exports dùng LocalObjectStore.

| Dataset / generation batch | Input load s | Stage + seal s | Validate + promote s | Replay s | CSV + Parquet export s | DB MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Public / 128 | 0.099 | 0.057 | 0.329 | 0.035 | 0.781 | 2.84 |
| Public / 512 | 0.102 | 0.062 | 0.388 | 0.037 | 0.870 | 2.84 |
| Internal / 128 | 0.401 | 2.335 | 13.768 | 1.198 | 1.737 | 62.50 |
| Internal / 512 | 0.448 | 1.847 | 21.542 | 1.250 | 1.557 | 62.45 |

Serving counts giữ nguyên giữa batches/replays: public 122 pairs/0 proposals/
122 cases; internal 5.281 pairs/8 proposals/5.273 cases. Export source coverage
PASS: public 864→864 rows; internal 4.213→4.205 rows. Internal CSV 620.208 bytes,
SHA-256 `dfd08396fe1978ed6ad370be051706133b5b4e76dcedae7134bd4a4254238ca6`.
CSV/Parquet checksums và complete manifests được đọc lại trước publication.

Stage durations internal batch 512: readiness 0,609s; write/validate 0,734s;
upload/readback 0,047s; export total 1,547s. Audit lưu thêm peak RSS, temporary
disk/spill, queue depth và waits. Serial export queue/wait = 0 theo thiết kế,
không phải network queue measurement. Không benchmark peak DB server RSS/CPU,
COPY/transaction contention hoặc PostgreSQL materialization: **NOT_TESTED**.

## Export memory stability

Full baseline đã chạy fresh-process probes: 128 và 4.096 singleton rows, mỗi
row chứa 4 KiB text; tăng khoảng 16 MiB CSV payload, batch 16 và 128.

| Batch | Python peak 128→4.096 rows, bytes | RSS growth 128→4.096 rows, bytes | Outcome |
| --- | --- | --- | --- |
| 16 | 4.508.560 → 5.197.167 | 10.080.256 → 10.723.328 | PASS |
| 128 | 4.538.805 → 5.233.157 | 10.829.824 → 11.460.608 | PASS |

Python growth <6 MiB và RSS-growth difference <32 MiB theo test budget. RSS
growth dựa trên high-water mark trước/sau export; không phải instantaneous RSS.
Đây là export regression evidence, không phải chứng nhận memory của matching
pipeline hay scale hàng triệu records.

## Tái tạo

Theo các commands của [SHADOW_DIFF](SHADOW_DIFF.md). JSON metrics nằm trong mỗi
`<case>/result.json`, `db-<dataset>-b<batch>/metrics.json`; memory properties trong
baseline `pytest.xml`. Dùng evidence directory mới khi chạy lại các failed cases.
Không so sánh cached/completed evidence với một run mới như một repeated trial.
Live R2 upload, PostgreSQL DB, triệu records, concurrent load, throughput/p95 và
capacity/resource SLA acceptance đều **NOT_TESTED**.
