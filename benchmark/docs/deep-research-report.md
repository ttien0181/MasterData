# Kế hoạch nghiên cứu và benchmark kiến trúc Entity Resolution / Matching của MasterOne

## Định hướng nghiên cứu

Từ sơ đồ và tài liệu V2 bạn gửi, mình sẽ **không benchmark theo kiểu “model A có F1 cao hơn model B hay không”**. Cách đó chưa đủ để chứng minh giá trị của kiến trúc này.

Kiến trúc của bạn thực chất có nhiều lớp độc lập:

**Profiling → Normalization → Blocking → Field Comparison → Fellegi–Sunter → Pair Routing → Graph/Constraints → All-pairs Cluster Validation → Human Review → Feedback.**

Tài liệu V2 cũng xác định khá rõ các điểm khác biệt quan trọng: bỏ weighted similarity cố định, chuyển sang Fellegi–Sunter; có term-frequency adjustment; tách pair scoring khỏi clustering; lưu persistent must-link/cannot-link; kiểm tra tất cả cặp trong cluster; và chỉ dùng LLM cho explainability/hard cases thay vì cho quyết định merge. fileciteturn0file0

Điểm rất quan trọng là **Fellegi–Sunter tự nó không phải phần mới nhất của kiến trúc**. Splink hiện đã cung cấp Fellegi–Sunter, unsupervised estimation, term-frequency adjustment, custom fuzzy comparison, blocking và pairwise match probability. Splink sau đó mặc định có thể cluster các edge vượt threshold bằng connected components. citeturn0search0turn0search1

Vì vậy, hypothesis trung tâm cần kiểm chứng sẽ là:

> **MasterOne có thể giữ hoặc tăng pair-level accuracy so với các baseline mạnh, nhưng đồng thời giảm false merge ở cấp cluster nhờ frequency/quality/time-aware evidence, persistent constraints và all-pairs validation.**

Đây mới là thesis benchmark có khả năng làm nổi bật thiết kế của bạn.

Mình đề xuất nghiên cứu theo sáu câu hỏi:

| Câu hỏi nghiên cứu | Điều cần chứng minh |
|---|---|
| Blocking có đủ tốt không? | Candidate recall cao trong khi giảm mạnh số pair cần xét |
| Pair scorer có tốt hơn heuristic không? | Precision/recall và calibration tốt hơn weighted score |
| Các feature đặc thù có giá trị không? | TF adjustment, hub detection, Vietnamese normalization, temporal evidence thực sự tạo lift |
| Cluster safety có đáng giá không? | Giảm transitive false merge so với connected components |
| Human-in-the-loop có hiệu quả không? | Giảm review workload mà vẫn đạt target precision |
| So với sản phẩm/baseline bên ngoài thế nào? | Chất lượng, latency, cost và explainability cạnh tranh |

## Ma trận baseline nên benchmark

Không nên đưa quá nhiều hệ thống vào vòng đầu. Nên có ba lớp baseline: **baseline nội bộ**, **open-source/research**, và **commercial product**.

### Nhóm baseline bắt buộc

| ID | Baseline | Vai trò trong benchmark | Mức ưu tiên |
|---|---|---|---|
| B0 | Exact/rule matching | Lower bound | Bắt buộc |
| B1 | Weighted similarity phiên bản cũ | Chứng minh V2 tốt hơn thiết kế trước | Bắt buộc |
| B2 | Splink / Fellegi–Sunter | Baseline gần kiến trúc MasterOne nhất | Bắt buộc |
| B3 | Dedupe | Supervised + active-learning baseline | Cao |
| B4 | Zingg | ML entity-resolution pipeline | Cao |
| B5 | Logistic Regression / LightGBM | Controlled supervised baseline | Cao nếu đủ label |
| B6 | Ditto | Transformer entity-matching baseline | Public benchmark |
| B7 | LLM pair matcher | Upper/alternative semantic baseline | Chỉ hard-case/public subset |
| B8 | AWS Entity Resolution | Product-level comparator | Nếu có account/budget |

**Splink là đối thủ tham chiếu số một.** Nó dùng Fellegi–Sunter, hỗ trợ term-frequency adjustment và unsupervised training, nên nếu MasterOne chỉ thắng một heuristic đơn giản thì chưa đủ thuyết phục. citeturn0search0

Nhưng Splink clustering mặc định sử dụng connected components trên các pair vượt threshold. Đây chính là nơi benchmark `constrained clustering + all-pairs validation` của bạn có ý nghĩa rõ nhất. citeturn0search1turn0search7

**Dedupe** đáng benchmark vì đại diện cho hướng supervised/active learning; thư viện có cơ chế lấy sample pair và active learner để thu thập nhãn match/non-match. citeturn0search11

**Zingg** cũng là baseline tốt vì pipeline của nó học cả blocking model lẫn similarity classifier và có interactive labeling. Tài liệu của Zingg cho biết blocking model nhằm giảm không gian so sánh xuống một phần nhỏ của Cartesian product trước khi similarity classifier chạy. citeturn2search0turn2search2

### Baseline deep learning và LLM

Ditto nên được dùng trên các public pairwise benchmark, không nhất thiết phải đưa vào production path. Ditto formulation entity matching thành sequence-pair classification với pretrained Transformers và đã được đánh giá rộng trên các benchmark truyền thống. citeturn0academia24

Để có một điểm so sánh hiện đại, nên thêm **LLM pair matcher zero-shot/few-shot trên một subset**. Không nên chạy LLM trên toàn bộ Cartesian candidate space vì chi phí và latency sẽ làm phép so sánh không công bằng với pipeline probabilistic. Các nghiên cứu gần đây cho thấy LLM có thể mạnh ở pair matching, nhưng vấn đề blocking, clustering và cost vẫn là phần quan trọng của một hệ thống ER thực tế. Ví dụ OpenSanctions Pairs 2026 báo cáo kết quả pairwise rất cao với LLM nhưng cũng nhấn mạnh nhu cầu chuyển trọng tâm sang blocking, clustering và uncertainty-aware review. citeturn3academia24

Điều này rất phù hợp với thesis của kiến trúc MasterOne.

### Product benchmark

AWS Entity Resolution là product benchmark khả thi nhất nếu nhóm có quyền sử dụng AWS. Nó hiện hỗ trợ rule-based matching và ML-based matching; Advanced Rules còn có exact/fuzzy functions và transitive matching. citeturn1search2turn1search6

Điều đáng kiểm tra đặc biệt là **transitive matching**: AWS có khả năng nối record theo quan hệ bắc cầu. citeturn1search11 Đây là một đối chứng rất tốt cho case mà MasterOne chủ động kiểm tra A–C sau khi có A–B và B–C.

Các hệ thống thương mại khác như Informatica có thể đưa vào phần **architecture/product comparison**, nhưng không nên cam kết quality benchmark nếu nhóm không có license/API/data export tương đương. Informatica có match rules, match scores và manual merge/review workflow, nên hữu ích cho feature matrix hơn là benchmark thuật toán thuần. citeturn2search12turn2search13turn2search14

## Thiết kế dataset và gold standard

Benchmark nên được chia làm **internal benchmark** và **public benchmark**, nhưng dùng cùng một evaluation harness.

### Dataset riêng của nhóm

Dataset nội bộ mới là bộ quyết định hệ thống có phù hợp với Master Data thực tế hay không.

Không nên random split các **pair** đơn thuần. Nếu cùng một entity xuất hiện ở train và test dưới các duplicate record khác nhau, benchmark supervised có thể bị leakage.

Nên tạo ít nhất ba test slice:

| Slice | Mục đích |
|---|---|
| IID/entity-disjoint | Đo chất lượng cơ bản |
| Cross-source | CRM ↔ ERP, ERP ↔ Billing, v.v. |
| Temporal/OOD | Record/entity mới hơn hoặc source chưa thấy |

Gold set nội bộ nên có cả **pair ground truth và cluster ground truth**.

Một thiết kế thực dụng cho vòng đầu:

- khoảng **3.000–5.000 pair được adjudicate**;
- khoảng **300–500 cluster** được kiểm tra đầy đủ;
- tăng tỷ trọng hard-negative thay vì random negative;
- đồng thời giữ một phần random sample để ước lượng metric trên distribution thật.

Pair sampling nên stratify theo các pattern chính trong V2:

`exact rare contact`, `shared phone`, `same address`, `DOB conflict`, `name typo`, `missing identifiers`, `cross-source inconsistent`, `transitive cluster`, `hub value`.

Đặc biệt phải đưa vào test set các counterexample mà kiến trúc này được thiết kế để xử lý:

```text
A-B = strong match
B-C = strong match
A-C = strong conflict
```

và:

```text
same phone
same address
different actual persons
```

Nếu thiếu hai loại này thì benchmark gần như không đo được lợi ích của Cluster Safety Agent.

Gold set nên được **freeze** trước khi threshold tuning cuối cùng. Điều này phù hợp với chính nguyên tắc trong thiết kế V2 rằng evaluation labels phải độc lập với operational feedback. fileciteturn0file0

### Public benchmark cốt lõi

Mình đề xuất không lấy một đống dataset giống nhau. Nên chọn các bộ đại diện cho các failure mode khác nhau.

| Dataset | Vai trò |
|---|---|
| FEBRL3 / FEBRL4 | Person record linkage, typo/missing, có ground truth |
| Splink historical_50k | Person deduplication có cluster-level truth và quy mô lớn hơn |
| DBLP–ACM | Structured clean-clean matching |
| Amazon–Google hoặc Walmart–Amazon | Product descriptions/noisy structured text |
| WDC Products | Generalization/unseen entities |
| OpenSanctions Pairs | Multilingual/cross-source/person & organization |
| Dataset synthetic Vietnamese của nhóm | Vietnamese-specific robustness |

FEBRL đặc biệt sát với bài toán person linkage. FEBRL3 có 5.000 record, 2.000 entity gốc và nhiều duplicate trên mỗi entity; FEBRL4 có hai bảng 5.000 + 5.000 record cho clean-clean linkage. citeturn4search4turn4search1

Splink cũng hiện cung cấp `historical_50k`, gồm 50.000 record được tạo từ dữ liệu historical persons với duplicate/noise và 5.156 unique entities, rất phù hợp để test cả pair và clustering. citeturn0search8

Bộ benchmark kinh điển của DeepMatcher cung cấp nhiều dataset labeled như DBLP–ACM, Amazon–Google, Walmart–Amazon, v.v.; các dataset này hữu ích để so với literature và các model như DeepMatcher/Ditto. citeturn3search0turn3search1

WDC Products nên dùng khi test khả năng generalization của learned matcher: benchmark này được thiết kế theo các chiều như corner cases, lượng training data và đặc biệt là unseen entities. citeturn0academia25

OpenSanctions Pairs 2026 rất đáng đưa vào **extended benchmark** vì có 755.540 labeled pairs từ 293 nguồn, 31 quốc gia, với multilingual/cross-script names, missing/noisy và set-valued attributes. Nó sát hơn các bộ product matching nếu nhóm muốn chứng minh khả năng xử lý person/organization đa nguồn. citeturn3academia24

### Vietnamese stress-test riêng

Do ưu thế tiềm năng lớn nhất của hệ thống là xử lý dữ liệu Việt Nam, cần một public-style synthetic benchmark riêng, chẳng hạn:

```text
Nguyễn Thị Minh Anh
Nguyen Thi Minh Anh
NGUYEN THI MINH ANH
Nguyễn Minh Anh
Nguyễn Thị M. Anh

090x ↔ +8490x
đầu số cũ ↔ đầu số mới

10 Nguyễn Trãi
10 Nguyen Trai
Số 10, Nguyễn Trãi

DOB:
01/02/1990
02/01/1990

shared family phone
shared company hotline
role-based email
old/new address
```

Mỗi transformation phải lưu `corruption_type` để cuối cùng có thể báo cáo:

> model nào hỏng vì bỏ dấu, model nào hỏng vì tên gần nhau, model nào hỏng vì shared contact, model nào hỏng vì transitivity.

Synthetic dataset này không thay cho dữ liệu thật; vai trò của nó là **controlled stress test**.

## Protocol thí nghiệm và ablation

Đây là phần quan trọng nhất của kế hoạch.

Không nên chỉ chạy:

> MasterOne full vs Splink vs Zingg.

Làm vậy sẽ không biết cải tiến đến từ đâu.

Cần benchmark **component-wise** và **end-to-end**.

### Blocking experiment

Cố định pair matcher, thay blocking algorithm.

So sánh:

```text
Exact blocking
vs
Multi-rule exact blocking
vs
TF-IDF char n-gram top-k
vs
MinHash/LSH
vs
Dense embedding ANN
vs
Hybrid exact + TF-IDF
vs
Hybrid exact + TF-IDF + ANN
```

Splink có sẵn công cụ phân tích số comparisons được tạo bởi blocking rule và largest blocks, nên có thể dùng làm reference để kiểm tra skew/hub blocks. citeturn0search3

Nếu cần benchmark filtering rộng hơn, literature benchmark hiện thường so cả blocking workflows, string similarity join và nearest-neighbour methods như MinHash LSH, FAISS, ScaNN hay DeepBlocker. citeturn3search5

Metric:

```text
Candidate Recall / Pair Completeness
Reduction Ratio
Candidates per record
P50/P95 block size
Runtime
Peak RAM
```

Điều kiện quan trọng nhất:

> **Blocking phải được tối ưu theo recall, không theo pair F1.**

### Pair matching experiment

Ở thí nghiệm này phải **cố định candidate pairs giống nhau cho tất cả matcher**.

Nếu Splink nhìn thấy candidate A-B nhưng MasterOne không nhìn thấy A-B, đó là vấn đề blocking chứ không phải pair scoring.

Ma trận:

```text
M0 Exact rules
M1 Weighted score V1
M2 Logistic Regression
M3 LightGBM
M4 Splink / Fellegi-Sunter
M5 MasterOne Fellegi-Sunter
M6 Dedupe
M7 Zingg
M8 Ditto
M9 LLM zero-shot/few-shot
```

Sau đó làm ablation MasterOne:

```text
A0 = weighted heuristic
A1 = FS comparison levels
A2 = A1 + term-frequency adjustment
A3 = A2 + hub/shared-value detection
A4 = A3 + quality flags
A5 = A4 + Vietnamese field normalization
A6 = A5 + temporal evidence
```

Ablation này trả lời câu hỏi quan trọng hơn F1 tổng:

> **mỗi thành phần mang lại bao nhiêu lợi ích và nó sửa loại lỗi nào?**

### Cluster experiment

Đây nên là experiment riêng biệt, vì pair F1 tốt không đảm bảo cluster tốt.

Cố định cùng một pair-score table rồi chạy:

```text
C0 Threshold + connected components
C1 Single-best-link / restricted linking
C2 Greedy constrained clustering
C3 C2 + persistent cannot-link
C4 C3 + invariant conflict checks
C5 C4 + all-pairs cluster validation
```

Splink connected-components là baseline tự nhiên cho C0. citeturn0search1 Splink cũng có single-best-links cho trường hợp các source được giả định duplicate-free, nên có thể đưa vào benchmark khi phù hợp. citeturn0search7

Dataset stress-test cần tăng chủ ý các cấu trúc:

```text
A≈B, B≈C, A≠C

A≈B, B≈C, C≈D
nhưng A và D có verified identifier conflict

hub record kết nối 20 người

shared phone tạo giant component
```

Nếu MasterOne có một contribution nổi bật, rất có thể nó sẽ xuất hiện ở đây chứ không phải ở pair F1.

### Human review experiment

So ba mode:

```text
Raw pair list sorted by probability
vs
Review Pattern Classifier + deterministic explanation
vs
Pattern + deterministic explanation + LLM explain-more
```

Không cho LLM biết ground truth.

Cần đo:

```text
review time/case
reviewer agreement
decision accuracy
DEFER rate
number of fields reviewer phải inspect
LLM unsupported-field rate
```

Thiết kế này sẽ kiểm chứng đúng vai trò của LLM trong V2: **hỗ trợ reviewer**, không phải thay matcher.

## Bộ metric và tiêu chí thành công

Không nên dùng một con số F1 tổng hợp để quyết định thắng/thua.

### Blocking

| Metric | Ý nghĩa |
|---|---|
| Candidate Recall | % true match còn sống sau blocking |
| Reduction Ratio | giảm bao nhiêu pair |
| Comparisons/record | computational load |
| P95 block size | phát hiện giant/hub blocks |
| Runtime | scalability |

Target khuyến nghị cho internal benchmark:

```text
Candidate Recall ≥ 99% trên gold pair
```

rồi mới tối ưu reduction ratio.

### Pair level

Cần báo cáo:

```text
Precision
Recall
F1
PR-AUC
False Merge Rate
False Non-Match Rate
Brier Score
Reliability / Calibration Curve
```

Điểm quan trọng nhất đối với master-data không nên là max F1.

Nên thêm metric vận hành:

> **Auto-merge coverage tại target precision.**

Ví dụ:

```text
Precision ≥ 99.5%
→ auto-merge được bao nhiêu % true matches?
```

Sau đó vẽ:

```text
precision target
vs
automation coverage
vs
review workload
```

Đây sẽ hữu ích cho business hơn rất nhiều so với việc chỉ nói F1 = 0.94.

### Cluster level

Bắt buộc có:

```text
B-cubed Precision
B-cubed Recall
B-cubed F1
Pairwise F1 trên transitive closure
Cluster purity
Over-merge rate
Under-merge rate
Giant-cluster incidence
Cannot-link violations
Strong-conflict-within-cluster rate
```

Một KPI đặc biệt nên định nghĩa:

```text
Unsafe Cluster Rate
=
clusters containing at least one verified/high-confidence contradictory pair
/
all predicted multi-record clusters
```

Đây chính là metric gần với thiết kế all-pairs validation nhất.

### Operational metrics

Đo end-to-end:

```text
records/sec
candidate pairs/sec
CPU-hours
GPU-hours
peak memory
wall-clock latency
storage
cloud/API cost
```

Với API/LLM:

```text
cost / 1,000 reviewed pairs
cost / 1 million source records
tokens / decision
P50/P95 latency
```

Không nên tuyên bố “model X tốt nhất” nếu F1 tăng 0,3 điểm nhưng chi phí tăng 50 lần.

## Lộ trình thực hiện và deliverables

Mình khuyến nghị chia thành **core benchmark bốn tuần** và một vòng mở rộng sau đó. Ba tuần vẫn có thể làm được bản tối thiểu, nhưng nếu chạy cả public benchmark, internal gold, clustering và product baseline thì bốn tuần thực tế hơn.

### Tuần đầu — Freeze benchmark

Mục tiêu tuần đầu không phải tuning model mà là đảm bảo mọi model được thi đấu trên cùng một sân.

Công việc:

```text
Audit dataset nội bộ
↓
Define entity types
↓
Define gold schema
↓
Build train/dev/test entity-disjoint splits
↓
Generate Vietnamese stress-test
↓
Download/freeze public datasets
↓
Build common benchmark interface
```

Output chính:

```text
datasets/
  internal/
    train
    dev
    test_frozen
  public/
    febrl
    historical50k
    dblp_acm
    ...
  synthetic_vn/

benchmark_manifest.yaml
```

Mỗi dataset cần metadata:

```yaml
task: dedupe | clean_clean | multi_source
entity_type: person | organization | product
ground_truth: pair | cluster
record_count:
entity_count:
positive_pairs:
sources:
```

### Tuần hai — Blocking và pair matching

Chạy toàn bộ blocking matrix trước.

Sau khi chọn 2–3 blocker hợp lý nhất, freeze candidate sets rồi benchmark:

```text
V1 weighted
Splink
MasterOne FS
LR/LightGBM
Dedupe/Zingg
```

Deep/LLM model chỉ cần chạy public datasets hoặc hard-case subset trong vòng đầu.

Deliverable:

```text
blocking_results.parquet
pair_results.parquet
calibration_report
error_buckets
```

Cùng một `pair_id` phải theo được qua tất cả model.

### Tuần ba — Cluster safety và ablation

Đây là tuần quan trọng nhất cho kiến trúc V2.

Chạy:

```text
Connected Components
vs
Constrained Clustering
vs
+ cannot-link
vs
+ all-pairs validation
```

Sau đó phân tích các lỗi theo graph pattern.

Deliverable quan trọng nhất có thể là bảng dạng:

| Method | Pair F1 | B³ F1 | Over-merge | Unsafe clusters |
|---|---:|---:|---:|---:|
| Connected components | … | … | … | … |
| Constrained | … | … | … | … |
| + all-pairs validation | … | … | … | … |

Nếu hypothesis của thiết kế đúng, bảng này có thể trở thành bằng chứng mạnh nhất cho V2.

### Tuần bốn — Product, review workflow và kết luận

Chạy product-level benchmark khả thi:

```text
Splink end-to-end
Zingg end-to-end
AWS Entity Resolution nếu có quyền
MasterOne end-to-end
```

Tách hai track để đảm bảo công bằng:

**Algorithm track**

```text
cùng normalization
cùng candidate set
→ chỉ so matcher/clustering
```

**Product track**

```text
cùng raw input
→ cho từng product dùng native preprocessing/pipeline
```

Nếu trộn hai track, rất khó biết một sản phẩm thắng nhờ normalizer hay matcher.

Cuối tuần chạy review experiment với khoảng 100–300 representative cases/reviewer nếu nguồn lực cho phép.

Deliverable cuối nên gồm:

```text
Executive benchmark
Algorithm benchmark
Cluster safety benchmark
Cost/performance benchmark
Error taxonomy
Ablation report
Recommendation matrix
Reproducibility package
```

Một bảng kết luận cuối cùng nên có dạng:

| Dimension | MasterOne | Splink | Zingg | AWS ER | Ditto/LLM |
|---|---|---|---|---|---|
| Vietnamese normalization | | | | | |
| Unsupervised cold start | | | | | |
| Pair calibration | | | | | |
| Hub/frequency handling | | | | | |
| Temporal evidence | | | | | |
| Cannot-link | | | | | |
| All-pairs cluster safety | | | | | |
| Human review | | | | | |
| Explainability | | | | | |
| Throughput | | | | | |
| Cost | | | | | |

**Tiêu chí go/no-go** mình sẽ đặt cho nghiên cứu là: MasterOne không nhất thiết phải có F1 cao nhất trên mọi public dataset. Nó cần chứng minh được ba điều: **pair performance ít nhất cạnh tranh với Fellegi–Sunter mạnh như Splink; giảm rõ rệt over-merge/unsafe clusters so với connected-components/transitive baselines; và đạt nhiều automation coverage hơn tại cùng một mức precision mục tiêu trên dataset nội bộ.** Đây là framing sát nhất với các quyết định kiến trúc V2 của nhóm. fileciteturn0file0