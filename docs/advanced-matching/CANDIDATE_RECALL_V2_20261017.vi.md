# Candidate Recall V2 — TC3, 17/10/2026

Mở rộng candidate engine bằng hai pass PERSON đã có trong nhánh thử nghiệm,
giữ nguyên scoring, policy, pair identity và Golden semantics. Mặc định
`CANDIDATE_RECALL_V2=false`; không kích hoạt mặc định sau benchmark.

[Evidence JSON](CANDIDATE_RECALL_V2_20261017.evidence.json) chứa các receipt
tổng hợp, hashes code triển khai và số liệu từng configuration.

## Ground truth và baseline

Baseline commit `90a8ed6`, đã gồm Round 3; working tree sạch trước nhiệm vụ.
Round 3 upload→CSV là 516,980 giây, thay cho mốc Round 2 598,601 giây.
Snapshot baseline: run `fc2c0187-4aae-41aa-ad66-6e32b652d17c`.

TC3 có 9.992 record CERTAIN, 5.128 entity: 4.864 entity hai record và 264
singleton. Vì vậy 4.864 là **tất cả tổ hợp cặp cùng entity**, không phải số
quyết định merge. Có 4.135 cặp PERSON và 729 ORGANIZATION. Policy hiện tại
chỉ cho gộp PERSON: denominator eligible là 4.135; denominator toàn bộ là
4.864. Không có positive cross-type. Candidate recall đo trước SCORE;
Pair/Cluster recall đo riêng sau quyết định/phân cụm.

Dùng đúng ID và normalized snapshot lấy bằng truy vấn read-only từ run cũ.
Container Linux tái tạo đúng cả tập 90.128 candidate: không thêm/mất cặp nào.
Windows/NumPy khác môi trường có thể chọn khác các TF-IDF tie; không dùng
kết quả đó để thay baseline Linux. Ground truth chỉ mở sau retrieval và
SCORE/DECIDE trong benchmark offline; input sản phẩm không chứa label.

## Root cause: toàn bộ 1.606 FN

| Lỗi trực tiếp ở TF-IDF | FN | Tỷ trọng |
| --- | ---: | ---: |
| DF pruning làm similarity của cặp bằng 0 | 1.202 | 74,84% |
| Partition theo năm sinh/province khác nhau | 395 | 24,60% |
| Top-k truncation | 9 | 0,56% |

Cả 1.606 cặp chỉ chung các rule block bị bỏ vì quá lớn. TF-IDF cũ dùng
`max_df=max(2, floor(0.05*N))` trong partition DOB/location nhỏ; nhiều n-gram
tên chung bị xóa. Do đó hạ threshold hoặc chỉ tăng k không giải quyết phần
lớn FN. 395 cặp sai năm sinh còn không được so sánh trong cùng partition.
Đường chạy RULE_WITH_EMBEDDING_EVIDENCE không kích hoạt hai pass recovery
đã có ở experiment E0; embedding chỉ đánh giá candidate có sẵn.

| Dạng dữ liệu, phân nhóm không giao nhau | FN | Tỷ trọng |
| --- | ---: | ---: |
| Cùng full DOB, tên thiếu/thừa token | 1.154 | 71,86% |
| Khác năm, trùng ngày/tháng sinh | 395 | 24,60% |
| Cùng DOB, biến thể tên còn lại | 57 | 3,55% |

Các dấu hiệu có giao nhau: 1.530 cặp có tên là tập token con của nhau;
76 cặp có cùng tập token; cả 1.606 còn ít nhất hai token chung và không có
phone/email VALID trùng nhau; không cặp nào có government ID trùng.
1.605 cặp khác địa chỉ. Accent folding/NFC đã hoạt động trong normalization;
không xác định được FN trực tiếp do khác dấu/Unicode trong lượt này.
Toàn bộ FN được xuất tại `artifacts/candidate-recall-20261017/baseline-fn.jsonl`,
kèm nguyên nhân, block bị loại và pass cứu được cặp. Không dùng bảng này khi inference.

## Thuật toán và thay đổi code

Hai pass bổ sung theo UNION, canonical pair dedup giữ nguyên:

1. `person_token_full_dob`: DOB đầy đủ + một token tên, không phụ thuộc thứ tự.
2. `person_tokens_month_day`: ngày/tháng + hai token, cho phép năm bị sai đi vào review.

Chỉ PERSON có name/DOB VALID, DOB đủ ngày/tháng, tên có 2–12 token chữ
phân biệt dài ít nhất hai ký tự. Folding dùng helper tiếng Việt hiện hữu;
không thay biểu diễn `name_unicode` gốc. Block tối đa 30 record; mọi block
quá lớn ghi log hash, size và lý do. Union có budget 200.000 cặp (hoặc
giới hạn caller nhỏ hơn); vượt budget raise lỗi, không publish phần bị cắt.
Block ceiling kiểm soát số cặp phát ra, không phải bảo đảm RSS cho mọi input.

| Module | Thay đổi |
| --- | --- |
| `pipeline/person_blocking.py` | Tách chung key generator từ recovery hiện hữu |
| `pipeline/candidate_engine.py` | Hai pass native, provenance, budgets, metrics |
| `matching_agents/candidate_union.py` | Dùng chung helper; dedup thứ tự pass khi native recovery bật |
| `config.py`, `engine_adapter.py` | Cờ opt-in; pin theo run; profile cũ thiếu cờ giữ OFF |
| `matching_foundation_service.py` | Snapshot cấu hình lấy đúng cờ trong engine context |
| `test_candidate_recall_v2.py` | 17 regression test cho native/Parquet/hybrid/flags/constraints |
| `scripts/benchmark_candidate_recall_v2.py` | Benchmark source-only, evaluation boundary sau compute |

Không thêm search service, dependency, LLM/embedding retrieval, UI hoặc threshold
matching mới. Review độc lập đã phát hiện và sửa hai lỗi integration: config
snapshot đọc cờ ngoài pinned context và pass_order lặp khi recovery có ở native engine.

## Ablation TC3

| Configuration | Candidate | TP | FP | FN | P | R | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A. Baseline | 90.128 | 3.258 | 86.870 | 1.606 | 3,6149% | 66,9819% | 6,8595% |
| B. Chuẩn hóa lại rồi retrieval cũ | 90.128 | 3.258 | 86.870 | 1.606 | 3,6149% | 66,9819% | 6,8595% |
| C. Exact identifiers hiện hữu | 90.128 | 3.258 | 86.870 | 1.606 | 3,6149% | 66,9819% | 6,8595% |
| D. Baseline + global fuzzy-name | 139.818 | 3.698 | 136.120 | 1.166 | 2,6449% | 76,0280% | 5,1119% |
| Chỉ full DOB + token | 91.904 | 4.469 | 87.435 | 395 | 4,8627% | 91,8791% | 9,2365% |
| E. Cả hai pass, được chọn | **93.656** | **4.864** | **88.792** | **0** | **5,1935%** | **100%** | **9,8741%** |

B/C là control: chuẩn hóa và exact phone/email/ID đã có, không chứng minh được
lợi ích khi thêm lần nữa. Không thêm identifier pass dư thừa. D dùng GlobalTfidfIndex
char_wb 3–4, top-k 10, min similarity 0,30, corpus tách PERSON/ORGANIZATION;
không giữ vì E tốt hơn cả recall, precision, candidate count và latency.

| Pass theo thứ tự thêm vào baseline | Tổng cặp pass | Overlap | Cặp mới | TP cứu thêm | TP/1.000 cặp mới |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full DOB + token | 4.420 | 2.644 | 1.776 | 1.211 | 681,87 |
| Ngày/tháng + hai token | 5.595 | 3.843 | 1.752 | 395 | 225,46 |
| Tổng | — | — | **3.528** | **1.606** | **455,22** |

Candidate tăng 3,91%; giữ toàn bộ baseline. PERSON recall 61,1608%→100%;
ORGANIZATION giữ 100%. Trung bình 9,373 cặp/record, degree trung bình 18,746,
p95 48, max 98; giảm hơn 99,8% so với toàn bộ NxN.
Full-DOB-only là ablation chiếu theo nguồn provenance sau khi sinh cả hai pass;
chỉ so sánh chất lượng, chưa đo riêng latency của pass này. Không có cấu hình
runtime riêng cho biến thể một pass.

## Downstream offline và kiểm tra bổ sung

| Dataset / kết quả máy trước review | Baseline | V2 |
| --- | --- | --- |
| TC3 Pair P/R/F1 | undefined / 0 / 0 | undefined / 0 / 0 |
| TC3 Cluster pairwise P/R/F1 | undefined / 0 / 0 | undefined / 0 / 0 |
| TC1 Pair P/R/F1 | 100% / 44,7487% / 61,8295% | giữ nguyên |
| TC1 Cluster pairwise P/R/F1 | 100% / 44,7487% / 61,8295% | giữ nguyên |

TC3 không có MERGEABLE tự động ở cả hai bản. Cả 1.606 cặp mới được SCORE/DECIDE
xử lý nhưng vẫn INSUFFICIENT_EVIDENCE. Retrieval recall tăng không đồng nghĩa
automatic merge recall tăng; giữ review và hard constraints.

TC1_easy_match có 10.022 record, 4.894 positive: candidate 83.135→87.099
(+4,77%), recall giữ 100%, precision 5,8868%→5,6189%; không thêm false merge.
Thuật toán không được tune theo TC1 trong nhiệm vụ này, nhưng pass cũ từng được
thử trên TC1; không gọi đây là held-out hoàn toàn mới. Chưa có seed mới.
Cả hai dataset là synthetic generator truth, chưa phải human gold độc lập.

## Full API → review → Golden → CSV

| Retrieval offline, 3 fresh process mỗi bản | Baseline | V2 |
| --- | ---: | ---: |
| Median generation | 2,715 s | 3,251 s |
| Max OS peak RSS trong ba lượt | 423,17 MiB | 449,25 MiB |
| SCORE + cluster offline, lượt có checksum quyết định | 13,723 s | 14,397 s |

Median overhead 0,537 giây cho 1.606 TP cứu thêm: khoảng 2.992 TP/giây thêm,
hoặc 61,51 điểm phần trăm recall/giây thêm. N=3 quá nhỏ để suy ra tail latency;
OS/file cache và tải host không được kiểm soát hoàn toàn. Không diễn giải các
con số này như bảo đảm latency sản xuất. Quyết định và score projection của
đúng 90.128 cặp baseline có checksum **giống hệt** trước/sau khi thêm recovery.

Run mới: `f58e89ce-efed-4ede-81f3-3cbed8e42aef`. Cùng full TC3 và fast mode,
không gọi advisory embedding/LLM; receipt `DEMO_ADVISORY_EMBEDDING_SKIPPED`.
Matching chạy trước khi script dùng truth để duyệt qua API; không ghi trực tiếp
business decisions vào DB. Không gọi chất lượng sau scripted review là độ chính
xác autonomous của model.

| Kết quả sau scripted truth review | Round 3 baseline | V2 |
| --- | ---: | ---: |
| Pair/Cluster pairwise TP / FP / FN | 2.529 / 0 / 2.335 | **4.135 / 0 / 729** |
| Pair/Cluster P / R / F1 | 100% / 51,9942% / 68,4161% | **100% / 85,0123% / 91,8991%** |
| Eligible PERSON recall | 61,1608% | **100%** |
| Golden rows | 7.463 | **5.857** |
| Record bảo toàn | 9.992 | **9.992** |
| False merge / policy violation | 0 / 0 | **0 / 0** |
| Upload → CSV | 516,980 s | **673,999 s — 11 phút 14 giây** |
| Backend peak sampled RSS | 567,22 MiB | **667,98 MiB** |
| Worker peak sampled RSS | 2.271,25 MiB | **2.364,95 MiB** |

End-to-end tăng 30,37% (+157,02 giây), vẫn dưới 720 giây. Phần lớn chi phí
thêm nằm ở review/cụm: 4.001 group approval thay 2.434, qua 81 batch và replay
từng request; chặng scripted review 194,334 giây, cluster/Golden 280,462 giây,
confirm/finalize 21,733 giây, export 62,245 giây. Không gán toàn bộ thời gian tăng
cho candidate generation. Đây là một lượt end-to-end, không phải p95.

Backend cap vẫn 1 GiB, worker cap 8 GiB; không OOM, không restart. RSS trên là
sampling khoảng 3 giây, có thể bỏ lỡ peak ngắn. Một thử nghiệm ép cả SCORE của
baseline vào 1 GiB đã OOM (exit 137); không tuyên bố toàn pipeline chạy trong
1 GiB. Retrieval riêng của cả hai bản chạy được với cap 1 GiB.

4.251 cụm không đổi membership có **tất cả giá trị CSV Golden bằng baseline**.
Export cũ của run FINALIZED vẫn tải đúng bytes khi cờ mới đang bật. History
download và replay execute trả đúng checksum CSV mới, audit `CSV_EXPORTED=1`,
`FEEDBACK_CAPTURED=93.656`, mọi operation hoàn tất. Không đổi survivorship;
chưa dùng field-level human gold độc lập cho các cụm mới.

## FN còn lại và test status

Candidate FN trên TC3: **0**. Final FN sau scripted review: **729**, toàn bộ
ORGANIZATION ngoài policy. Không nới scope để biến chúng thành merge. Trước
review, matching tự động vẫn miss cả 4.864 positive như đã nêu ở trên.
Chưa đánh giá seed mới, các tên alias không chung token hoặc DOB thiếu/sai
ngày-tháng trên một bộ hard mới; không suy rộng 100% này sang dữ liệu thực tế.

**PASS:** 108 targeted tests, trong đó 17 test mới; TC3 API end-to-end; TC1
offline quality; đối chiếu Golden; history/export replay; old FINALIZED export.
Ruff cho toàn bộ file Python sửa/thêm và `git diff --check` đều pass. Không
tuyên bố chạy toàn bộ test suite của repository. Evidence tổng hợp được lưu
cạnh tài liệu này; receipt chi tiết tại `artifacts/candidate-recall-20261017/` và
`artifacts/full10k-20261016/candidate-recall-v2-final-20261017/`.

## Vận hành và rollback

Đặt `CANDIDATE_RECALL_V2=true` trong môi trường backend/worker rồi tạo profile/run
mới để opt in; với Compose hiện tại có thể dùng `.env.runtime.local`. Cờ không
tự bật cùng `MDM_ENGINE_V2_ENABLED`. Giữ default false trong source và example.

Rollback cấu hình: đặt `CANDIDATE_RECALL_V2=false`, restart backend/worker, tạo
profile/run mới. Run đã pin true tiếp tục dùng cấu hình đã pin; không sửa run
FINALIZED để thay candidate. Rollback code dùng `git revert` commit của thay đổi.
Source-hash fences hiện hữu vẫn có thể từ chối mutation/recompute của artifact
trước deployment (`WORKFLOW_CONFIG_CHANGED`); không nới fence để ép replay.

Reproduce bằng `scripts/benchmark_candidate_recall_v2.py --mode baseline|v2`
với `--snapshot`, `--dataset`, `--output` và tùy chọn `--score`. Các receipt
offline dùng fresh container Linux, 1 CPU, network none; retrieval cap 1 GiB,
score cap 8 GiB như worker hiện hữu. Unit tests chạy Windows/Python 3.13.
Sau đo đã khôi phục backend/worker QA về image `p119-backend:openrouter-mvp`
ban đầu; không để cờ true trong môi trường đang chạy.
