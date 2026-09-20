**Đề tài DATA-17**

**Agent phát hiện dữ liệu trùng lặp và quản lý Master Data**

_Nhóm T119: LNTT.exe_

# Phần 1. 1-Page Brief

**Định vị:** AI Agent phát hiện dữ liệu trùng, chuẩn hóa và xây dựng Golden Record có kiểm soát (HITL)

### Bối cảnh & vấn đề

Tập đoàn X có 3 hệ thống (CRM, ERP, Billing, Excel), trong đó thông tin của nhiều khách hàng và nhà cung cấp có thể tồn tại đồng thời. Việc thiếu một bản ghi master (golden record) đáng tin cậy gây ra:

- Báo cáo 360 độ sai lệch do đếm trùng khách hàng/nhà cung cấp
- Chăm sóc khách hàng trùng lặp, trải nghiệm không nhất quán
- Khó tích hợp dữ liệu giữa các hệ thống nội bộ
- Data Steward phải kiểm tra thủ công từng ca nghi trùng, tốn thời gian và dễ merge nhầm

### Giải pháp

Kết hợp hai năng lực thành một pipeline liền mạch, trong đó Entity Resolution là bài toán chính, còn Standardization là lớp chuẩn hóa đầu vào hỗ trợ nó:

- Standardization Agent: phát hiện định dạng lỗi (tên, số điện thoại, địa chỉ, ngày tháng), đề xuất & áp dụng rule chuẩn hóa có kiểm soát (preview trước khi áp dụng)
- Entity Resolution: gồm 2 agent phối hợp — Rule/Exact-Match Agent (matching key, business rule) và Embedding/Semantic Agent (fuzzy + vector similarity) — kết quả được hợp nhất bởi một cơ chế Decision Arbiter, sinh candidate (blocking), tính độ tương đồng, giải thích lý do khớp
- Golden Record Agent: tổng hợp bản ghi master theo chính sách quản trị (source priority), có provenance từng trường
- HITL: Data Steward và Data Approver phê duyệt các bước rủi ro (rule mới, merge, merge rủi ro cao); Viewer chỉ xem, không thao tác

Câu chuyện sản phẩm: Dirty data → Standardized data → Resolved entities → Trusted Golden Record.

### Đối tượng sử dụng

- Data Steward — vận hành hàng ngày: upload dữ liệu, duyệt rule chuẩn hóa, xử lý hàng đợi trùng lặp
- Data Approver / Data Manager — phê duyệt merge rủi ro cao, xem audit log, quản lý policy, rollback/split
- Viewer — chỉ xem dashboard, cluster detail và audit log; không có quyền chỉnh sửa, duyệt hay merge

### Giá trị kỳ vọng

- Giảm thời gian review thủ công
- Giảm tỉ lệ dữ liệu trùng chưa xử lý
- Giảm rủi ro gộp sai
- Tăng tỉ lệ dữ liệu có golden record đáng tin cậy

### Phạm vi MVP

2 nguồn CSV (CRM, ERP) → Standardization (preview + HITL) → Blocking → Matching (Rule Agent + Embedding Agent + Arbiter) → Golden Record proposal → Steward review → Merge → Audit + Rollback/Split. Không xây Identity Graph hay realtime resolution ở giai đoạn này.

# Phần 2. Product Requirements Document (PRD)

## 2.1 Tổng quan

MasterOne là nền tảng AI-powered Master Data Resolution có Data Quality Gate, giúp doanh nghiệp hợp nhất dữ liệu khách hàng/nhà cung cấp phân mảnh trên nhiều hệ thống thành một golden record đáng tin cậy, có kiểm soát và có thể truy vết (audit/rollback/split).

## 2.2 Vấn đề & pain point

| **Vấn đề**                               | **Mô tả**                                                                                                 | **Được xử lý bởi**                                         |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Dữ liệu không chuẩn                      | Cùng một giá trị nhưng viết khác nhau: tên có/không dấu, số điện thoại có/không mã vùng, địa chỉ viết tắt | Standardization Agent                                      |
| Không xác định được cùng thực thể        | Sau khi chuẩn hóa vẫn cần xác định các bản ghi có phải cùng một khách hàng/NCC hay không                  | Entity Resolution (Rule Agent + Embedding Agent + Arbiter) |
| Không có nguồn chân lý (source of truth) | Nhiều hệ thống có giá trị mâu thuẫn cho cùng một trường (VD: Tax ID)                                      | Golden Record Agent + Governance RAG                       |
| Rủi ro merge sai                         | Merge thủ công hoặc tự động không kiểm soát có thể gộp nhầm hai thực thể khác nhau                        | HITL (Steward + Approver) + Audit/Rollback/Split           |

## 2.3 Vai trò & quyền hạn

<div class="joplin-table-wrapper"><table><thead><tr><th><p><strong>Vai trò</strong></p></th><th><p><strong>Trách nhiệm chính</strong></p></th><th><p><strong>Quyền hạn</strong></p></th></tr></thead><tbody><tr><td><p>Data Steward</p></td><td><ul><li>Kết nối/upload dữ liệu</li><li>Xem lỗi chuẩn hóa &amp; duplicate candidates</li><li>Xem giải thích matching</li><li>Sửa golden record, submit merge</li><li>Xử lý case AI không chắc chắn</li></ul></td><td><p>Đề xuất &amp; thao tác thường ngày, không tự duyệt merge rủi ro cao</p></td></tr><tr><td><p>Data Approver / Manager</p></td><td><ul><li>Duyệt rule chuẩn hóa mới</li><li>Duyệt merge rủi ro cao</li><li>Xem audit log, rollback, duyệt split</li><li>Quản lý policy &amp; theo dõi chất lượng dữ liệu</li></ul></td><td><p>Quyền phê duyệt cao nhất, chịu trách nhiệm governance</p></td></tr><tr><td><p>Viewer</p></td><td><ul><li>Xem Dashboard, Cluster Detail, Golden Record, Merge History</li><li>Xem báo cáo Evaluation</li><li>Không upload, không chỉnh sửa, không duyệt</li></ul></td><td><p>Chỉ đọc (read-only) trên toàn hệ thống</p></td></tr></tbody></table></div>

## 2.4 Kiến trúc luồng xử lý

Chi tiết trực quan xem tại Phần 3 (Wireframe/UI Flow). Tóm tắt các bước:

- ① Data Ingestion — nạp dữ liệu từ CRM/ERP/Billing/CSV
- ② Data Profiling / Quality — phát hiện vấn đề định dạng
- ③ Standardization Agent — chuẩn hóa tên, SĐT, email, địa chỉ, ngày tháng; xuất Raw + Normalized + Rule + Quality Flag (không ghi đè raw)
- HITL #1 — Steward/Approver duyệt rule chuẩn hóa qua Preview Diff (before → after)
- ④ Candidate Generation / Blocking — giảm số cặp cần so khớp ở quy mô lớn
- ⑤ Matching — Rule/Exact-Match Agent và Embedding/Semantic Agent chạy song song trên cùng tập candidate; mỗi agent trả về điểm số + evidence riêng
- ⑤.1 Decision Arbiter — hợp nhất kết quả 2 agent (weighted ensemble theo loại trường), sinh match confidence cuối cùng kèm lý do tổng hợp; nếu 2 agent bất đồng vượt ngưỡng, case được gắn cờ "cần review kỹ" trước khi vào hàng đợi Steward
- ⑥ Duplicate Clustering — nhóm các bản ghi được xác định là cùng thực thể
- ⑦ Golden Record Agent — dùng Governance RAG (source priority, merge policy) để đề xuất bản ghi master
- HITL #2 — Steward reject/edit/approve đề xuất golden record
- HITL #3 — Approver duyệt merge nếu được đánh giá rủi ro cao
- Merge Record → Golden Record → Audit Log + khả năng Rollback/Split
- Human Feedback → Active Learning Loop → cải thiện threshold/rule theo thời gian (xem 2.5.4)

## 2.5 Yêu cầu chức năng

### 2.5.1 Standardization Agent

- profile_data() — phân tích chất lượng từng cột dữ liệu
- normalize_name(), normalize_phone(), normalize_address() — chuẩn hóa theo rule
- preview_standardization() — hiển thị before/after trước khi áp dụng toàn bộ
- Giữ nguyên raw_value song song với normalized_value, kèm rule_applied và quality_flag

### 2.5.2 Entity Resolution — Multi-Agent Matching

Thay vì gộp toàn bộ logic matching vào một agent duy nhất, hệ thống tách thành 2 agent chuyên trách và 1 lớp hợp nhất quyết định, đúng theo yêu cầu "agent embedding + agent rule kết hợp quyết định":

| **Thành phần**           | **Nhiệm vụ**                                                                                                                                                                                          | **Output**                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Rule/Exact-Match Agent   | So khớp theo blocking key, business rule (VD: trùng Tax ID, trùng số điện thoại + họ tên chuẩn hóa)                                                                                                   | rule_score (0/1 hoặc theo trọng số rule), rule_matched\[\]                                        |
| Embedding/Semantic Agent | Sinh embedding cho tên/địa chỉ, tính cosine similarity trong vector DB; kèm fuzzy string matching (RapidFuzz) làm tín hiệu bổ sung                                                                    | embedding_score, fuzzy_score, nearest_neighbors                                                   |
| Decision Arbiter         | Hợp nhất rule_score + embedding_score theo trọng số cấu hình được theo từng loại trường (VD: Tax ID ưu tiên rule, tên/địa chỉ ưu tiên embedding); sinh final_confidence + reasoning tổng hợp bằng LLM | final_confidence, match_reason (giải thích tự nhiên), flag_conflict (nếu 2 agent lệch nhau nhiều) |

- generate_candidates() — blocking để giảm số cặp cần so khớp
- calculate_similarity() — exact match (Rule Agent)
- semantic_match() — fuzzy + embedding similarity (Embedding Agent)
- resolve_decision() — Decision Arbiter hợp nhất 2 luồng điểm số, áp dụng trọng số theo loại trường
- cluster_records() — gom cụm các bản ghi trùng dựa trên final_confidence
- Mỗi kết quả matching phải có confidence score và lời giải thích (evidence-based reasoning), bao gồm cả trường hợp 2 agent bất đồng

### 2.5.3 Golden Record & Governance

- retrieve_governance_policy() — truy xuất chính sách từ RAG (Master Data Policy, Source Priority, Merge Policy)
- build_golden_record() — tổng hợp bản ghi master, mỗi trường phải có provenance (nguồn gốc)

### 2.5.4 Active Learning từ phản hồi Steward (thiết kế mức MVP)

Được đưa vào thiết kế ngay từ MVP thay vì chỉ nằm ở roadmap, vì đây là yêu cầu Nâng cao của đề bài. Ở mức MVP, cơ chế hoạt động ở dạng đơn giản, chưa cần train lại model:

- Mỗi quyết định Approve/Reject của Steward tại HITL #2 được ghi lại kèm final_confidence, rule_score, embedding_score tại thời điểm đề xuất (log_feedback())
- Định kỳ (batch, VD hàng tuần), một job thống kê tính lại điểm precision/recall tại các mốc ngưỡng khác nhau dựa trên nhãn thực tế (Approve = positive, Reject = negative) → đề xuất ngưỡng confidence mới cho từng loại trường (suggest_threshold())
- Đề xuất ngưỡng mới không tự áp dụng — phải qua Data Approver duyệt trước khi cập nhật vào Governance Policy (giữ nguyên tinh thần HITL, không để hệ thống tự thay đổi hành vi mà không kiểm soát)
- Giai đoạn V3 (sau MVP) mới mở rộng sang học có giám sát thực sự (re-train embedding/threshold model tự động) như mô tả ở Roadmap 2.11

### 2.5.5 Action & Governance an toàn

- merge_records() — chỉ thực hiện sau khi có phê duyệt phù hợp với mức rủi ro
- rollback_merge() — khôi phục trạng thái golden record về đúng thời điểm trước merge (dùng khi phát hiện sai gần như ngay sau khi merge, chưa có dữ liệu mới ghi đè lên golden record)
- split_golden_record() — tách một golden record đã tồn tại một thời gian trở lại thành các bản ghi gốc, áp dụng khi: (a) phát hiện gộp sai sau khi đã có giao dịch/dữ liệu mới phát sinh trên golden record, hoặc (b) chính hai thực thể từng đúng là một nhưng nghiệp vụ thay đổi (VD: tách công ty). Khác với rollback, split phải xử lý việc phân bổ lại dữ liệu phát sinh sau merge (transaction, ticket, log tương tác) về đúng bản ghi gốc dựa trên provenance từng trường, và luôn yêu cầu Data Approver duyệt (không cho Steward tự thực hiện)
- Toàn bộ hành động (merge, rollback, split) ghi vào Audit Log (đề xuất AI, quyết định Steward, quyết định Approver, lý do, thời gian)

## 2.6 Yêu cầu phi chức năng

- Khả năng mở rộng: xử lý được tập dữ liệu lớn (blocking bắt buộc để kiểm soát chi phí tính toán)
- An toàn dữ liệu: raw data không bao giờ bị ghi đè; mọi thay đổi có thể truy vết và hoàn tác
- Phân quyền: các tool hành động (merge, rollback, split) phải kiểm tra vai trò trước khi thực thi; Viewer bị chặn ở mọi tool hành động
- Chống prompt injection: dữ liệu nguồn và nội dung policy được coi là dữ liệu, không phải chỉ thị cho agent
- Khả năng giải thích: mọi đề xuất matching/golden record phải có lý do kèm theo, không phải hộp đen
- Kiểm soát chi phí: giới hạn số lần gọi LLM reasoning trên mỗi batch/ngày; ưu tiên rule-based/embedding nhẹ cho bước lọc, chỉ gọi LLM lớn ở bước cần giải thích ngôn ngữ tự nhiên hoặc case bất đồng giữa 2 agent

## 2.7 State machine (vòng đời một job)

IMPORTED → PROFILING → STANDARDIZATION_REVIEW → STANDARDIZED → BLOCKING → MATCHING → ARBITRATION → CLUSTERING → GOLDEN_RECORD_PROPOSED → HUMAN_REVIEW → (APPROVED → MERGED) hoặc (REJECTED); từ MERGED có thể chuyển sang SPLIT_REQUESTED → SPLIT_APPROVED → SPLIT khi phát hiện gộp sai.

## 2.8 Kiến trúc kỹ thuật đề xuất

| **Thành phần**      | **Lựa chọn đề xuất**                                                                                                                                |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend            | React / Next.js — Dashboard, Data Sources, Standardization Review, Duplicate Queue, Cluster Detail, Golden Record Review, Merge History, Evaluation |
| Backend             | Python + FastAPI                                                                                                                                    |
| Agent orchestration | LangGraph (hỗ trợ state, branching, HITL, multi-agent: Rule Agent, Embedding Agent, Decision Arbiter chạy như các node riêng)                       |
| Xử lý dữ liệu       | Pandas / Polars; DuckDB nếu cần scale lớn hơn                                                                                                       |
| Entity matching     | RapidFuzz, Splink/RecordLinkage (Rule Agent); embedding model (Embedding Agent)                                                                     |
| Vector DB           | Qdrant — cho embedding và governance RAG                                                                                                            |
| Cơ sở dữ liệu       | PostgreSQL — jobs, records, clusters, approvals, audit log, feedback, split history                                                                 |
| Triển khai          | Docker + Cloud Run (hoặc nền tảng tương đương)                                                                                                      |

## 2.9 Chỉ số đánh giá & benchmark

Đánh giá theo từng giai đoạn của pipeline, không chỉ đánh giá kết quả cuối:

| **Giai đoạn**              | **Chỉ số chính**                                                           |
| -------------------------- | -------------------------------------------------------------------------- |
| Standardization            | Normalization accuracy, Invalid-format detection accuracy, Human edit rate |
| Blocking                   | Blocking Recall, Candidate Reduction Ratio                                 |
| Matching (Rule Agent)      | Precision, Recall theo rule-matched pairs                                  |
| Matching (Embedding Agent) | Precision, Recall theo embedding-matched pairs                             |
| Arbitration                | Precision, Recall, F1 sau hợp nhất; tỉ lệ case bị flag conflict            |
| Merge                      | False Merge Rate (chỉ số quan trọng nhất)                                  |
| Golden Record              | Field Accuracy, Source Selection Accuracy                                  |
| Con người                  | Acceptance Rate, Correction Rate, Review Time                              |

### 2.9.1 Tập dữ liệu gán nhãn cho đánh giá

Để tính được precision/recall/F1 như yêu cầu, nhóm đề xuất xây dựng tập test gán nhãn như sau (số liệu cụ thể sẽ chốt sau khi có dữ liệu fixture thật):

- Nguồn: benchmark công khai (Walmart–Amazon, Abt–Buy, Amazon–Google, DBLP–ACM) dùng để kiểm chứng thuật toán trên tập chuẩn có sẵn nhãn; song song là fixture nghiệp vụ riêng (CRM/ERP) mô phỏng dữ liệu tiếng Việt do nhóm tự tạo và gán nhãn thủ công
- Với fixture nội bộ: đề xuất tối thiểu vài trăm cặp, cân bằng giữa positive (cùng thực thể) và negative (khác thực thể, kể cả các case khó — trùng tên khác người, khác tên cùng người) để tránh đánh giá lạc quan giả
- Người gán nhãn: thành viên nhóm đóng vai Data Steward gán nhãn độc lập, có đối chiếu chéo (cross-check) trên một tập con để đo độ đồng thuận giữa người gán nhãn trước khi dùng làm ground truth
- Tập test được giữ cố định (hold-out), không dùng để tinh chỉnh threshold, nhằm đánh giá khách quan hiệu năng Arbiter

Baseline so sánh: (1) Exact match, (2) Fuzzy only, (3) Fuzzy + Embedding, (4) Proposed — Standardization + Smart Blocking + Rule Agent + Embedding Agent + Decision Arbiter + Governance + Active Learning từ Human Feedback.

## 2.10 Failure cases cần kiểm thử

- Tên giống nhau nhưng khác người
- Tên khác nhau nhưng cùng một người
- Số điện thoại dùng chung trong gia đình/tổ chức
- Thiếu số điện thoại/email
- Giá trị mâu thuẫn giữa các nguồn
- Chuẩn hóa sai (wrong normalization)
- Merge sai → cần rollback hoặc split (tùy đã phát sinh dữ liệu mới hay chưa)
- Rule Agent và Embedding Agent bất đồng mạnh (VD: rule nói khớp nhưng embedding nói không, hoặc ngược lại)
- Tool timeout
- Prompt injection trong dữ liệu nguồn hoặc trong nội dung policy

## 2.11 Roadmap sau MVP

V1: Core DATA-12 + DATA-17 (đã gồm Active Learning mức cơ bản trong MVP) → V2: Source Trust → V3: Active Learning nâng cao (re-train tự động) → V4: Temporal Identity → V5: Identity Graph → V6: Realtime Entity Resolution → V7: Master Data Intelligence Platform.

Ưu tiên mở rộng đầu tiên sau MVP: Source Trust — vì Active Learning mức cơ bản đã được đưa vào MVP (2.5.4), bước tiếp theo gần với core nhất là Source Trust, giúp tăng độ tin cậy của Golden Record Agent khi các nguồn có lịch sử sai khác nhau.
