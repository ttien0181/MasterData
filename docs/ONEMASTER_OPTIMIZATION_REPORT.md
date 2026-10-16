# OneMaster optimization — 16/10/2026, lượt phục hồi candidate

## Phạm vi và kết luận

Đã triển khai pass blocking PERSON độc lập với thứ tự token tên; giữ default tắt, không đổi scoring, model, ngưỡng, constraints, quyền ORGANIZATION hoặc auto-merge. Kết quả đo bên dưới là **offline trên toàn bộ source-only CSV**, không phải nghiệm thu HTTP → FINALIZED mới. Truth được mở sau retrieval, scoring và guarded clustering; không dùng truth-assisted replay.

Báo cáo này bổ sung cho [báo cáo tối ưu trước](../ONEMASTER_OPTIMIZATION_REPORT.md) và [nghiệm thu HTTP trước](advanced-matching/E2E_ACCEPTANCE_TC1_TC3_20261016.vi.md). Các cải tiến cosine/checkpoint/bulk/snapshot và số đo HTTP cũ thuộc lượt trước, không tính lại là thành quả của lượt này. Không sửa nội dung nghiệm thu lịch sử để biến recall cũ thành PASS.

Working tree đã có nhiều thay đổi trước khi bắt đầu. Không reset, commit, migration, đổi resource limits, tạo Compose mới, sửa dataset/truth hoặc kích hoạt profile trên stack đang dùng. Docker daemon truy cập được; `docker compose ps --format json` không có service chạy tại thời điểm kiểm tra. Kiểm thử API/worker dùng DB/artifact fixture cô lập; chưa chạy full10k HTTP trên code recovery.

## Audit implementation và nguyên nhân

| Thành phần | Bằng chứng trong code | Quyết định của lượt này |
| --- | --- | --- |
| Chuẩn hóa | `pipeline/standardization.py`: Unicode, phone legacy, chất lượng field, parsed DOB, verification riêng với VALID | Tái sử dụng fold/parse; không suy VALID thành verified |
| Exact/composite blocking | `pipeline/candidate_engine.py`: exact phone/email/ID, prefix/surname + year, DOB + street, block caps và canonical provenance | Giữ nguyên. Prefix/surname không bất biến khi đảo tên hoặc mất tên đệm |
| Sparse retrieval | `pipeline/tfidf_blocking.py`: legacy name partition theo năm/tỉnh; `GlobalTfidfIndex` char ngram 3–4, unique text postings, tiled top-K | Giữ rollback. Không dò K/threshold/ngram theo TC3; global lexical và fallback chưa chứng minh tradeoff tốt dưới cap mặc định |
| Hybrid/dense | `matching_agents/candidate_union.py`, `retrieval.py`: E0/E1/E2/E3, exact tiled vector retrieval, budgets và retained history | Thêm PERSON recovery vào union, provenance và budget chung; chưa thêm FAISS/HNSW/BM25/Lucene |
| Scoring/Splink | `pipeline/scoring_engine.py`, `pair_assessment.py`, `review_recovery.py`: rule/Splink evidence, insufficient floor và review support | Không thay kết luận nghiệp vụ để làm đẹp recall. Retrieval tìm lại pair không đồng nghĩa pair được merge |
| Embedding | `embedding_agent.py`, `openrouter_embedding.py`, `workflow_agents.py`: dedup text, vector cache owner/run/model/preprocess, batch checkpoints, sequential requests/retry/caps | Đã có tối ưu; không thêm concurrency hoặc model. E0/E1 bỏ retrieval/evidence provider theo luồng DECIDE sẵn có |
| Cluster/Golden | `clustering.py`, `decision_semantics.py`, `workflow_automation.py`: full guard, verified conflicts, cannot-links, review/certificate | Giữ nguyên. Test mới chứng minh recovery không vượt verified DOB conflict hoặc Deny |
| Snapshot/storage | `workflow_snapshot_producers.py`, `workflow_snapshot_storage.py`: cursors, projection, immutable partition/manifest; uploader queue hữu hạn | Không refactor lại. Snapshot vẫn cần global consistency và hashing; chưa có SQL/network profile mới để chứng minh thay đổi lớn |
| Review API | `workflow_bulk.py`, `steward_feedback.py`: prefetch và batch flush đã có | Không tạo bulk API trùng lặp; bộ regression kiểm tra các cơ chế hiện có |
| Provider failures | `workflow_pair_advice.py`, `llm/pair_advice_contract.py`: schema, symbolic evidence, unavailable fallback | Giữ fail-safe. Không đổi SCHEMA_INVALID thành evidence hợp lệ |
| Worker/retry | `resolution_worker.py`, `workflow_jobs.py`, `workflow_candidate_rebuild.py`: leases, fences, immutable bindings | Sửa rebuild E0/E1 để giữ cùng chính sách không gọi encoder như DECIDE |

`ARCHITECTURE.md` vẫn mô tả SQLite và migration 0001–0004; implementation hiện có workflow v2, matching registry, worker, PostgreSQL QA và partitioned artifacts. Audit này căn cứ code hiện tại; không dùng tài liệu cũ để suy đoán framework/module.

### P0: mất candidate do key phụ thuộc vị trí tên

Ở baseline offline TC3: 1.608 positive pairs chưa được tìm thấy; 1.213 cặp có cùng DOB đầy đủ hợp lệ. Ví dụ cơ chế tổng quát: `Nguyễn Văn Bình` ↔ `Bình Nguyễn` không giữ prefix/surname cũ; nếu năm sinh còn sai thì partition theo năm cũng không so sánh hai record. Đây là lỗi coverage retrieval; embedding evidence chạy sau candidate không thể cứu những cặp chưa được phát.

Thêm hai rule version `person_name_dob_v1`, chỉ PERSON có name/DOB VALID và DOB đầy đủ parse hợp lệ:

- `person_token_full_dob`: mỗi token tên đã fold + DOB đầy đủ.
- `person_tokens_month_day`: mỗi cặp token tên không phụ thuộc thứ tự + ngày/tháng sinh. Sai năm chỉ được đưa vào review, không được bỏ qua DOB conflict.

Chỉ dùng token chữ dài ít nhất 2 ký tự, từ 2 đến 12 token khác nhau; block tối đa 30 record theo ceiling composite hiện có. Các giới hạn cố định trước khi đo lại, không grid-search theo truth. Không thêm DOB/name rule cho ORGANIZATION. Empty/invalid/partial/placeholder bị loại khỏi pass mới; legacy pairs vẫn giữ nguyên.

Union giữ canonical IDs và toàn bộ nguồn; provenance chỉ chứa hash block, không chứa tên/ngày sinh. Mỗi pass có số candidate mới và positive mới trong phần đánh giá offline. Block quá lớn có count/reason, không truncate để âm thầm công bố một tập partial. Candidate cap vượt giới hạn thì operation thất bại.

### Hai lỗi resource/lifecycle được sửa thêm

1. `build_union` trước đây dùng `CandidateEngine()` với cap 50 triệu, rồi mới kiểm cap union 200.000. Đã truyền cap được pin vào engine ngay từ đầu, giữ lỗi `CANDIDATE_BUDGET_EXCEEDED`; tránh cấp phát candidate/provenance quá lớn trước khi từ chối.
2. Recovery mới được rà soát để kiểm thời gian/workspace ngay trong indexing và ghi log, kể cả không phát cặp nào. Postings của block quá lớn bỏ IDs sau record thứ 30, giữ exact count. Estimate tính cả keys/postings/log và từng provenance observation trùng pair, không chỉ unique pair. Đây là estimate allocation bảo thủ, không phải giới hạn RSS toàn process; RSS guard worker hiện hữu vẫn cần thiết.
3. API mới cho phép chọn experiment E0/E1 vốn đã được runtime hỗ trợ. Đường rebuild cũ lại luôn gọi vector/evidence/coordinator. Test tái hiện encoder bị gọi trong rebuild E0; đã sửa guard giống DECIDE. E2/E3 tiếp tục đường provider/coordinator cũ.

## Chất lượng embedding và giới hạn mô hình

Receipt lịch sử `run-03-e2e/TC3_hard_match/embedding-quality.json`: name positive/negative mean cosine 0,64779/0,68580; address 0,86948/0,88047; AUC 0,45669/0,45538. Toàn bộ 90.128 candidate có evidence OK, name có 5.000 unique text, address chỉ 499 trên 9.992 record. Vì vậy missing embeddings không giải thích AUC thấp của lượt đó. Source TC3 có tên rút gọn/đảo và địa chỉ cùng mẫu đường với số nhà khác nhau; đây là giả thuyết về mismatch semantic/identity và mật độ negative tương tự, chưa phải ablation xác nhận nguyên nhân mô hình.

Cosine được kiểm tra theo hướng similarity cao hơn; không đảo dấu, đổi model hoặc calibrate theo test. Golden replay lịch sử TC3 recall 51,994% không phải accuracy tự động. Benchmark mới đo guarded rule proposals riêng; không gọi chúng là Golden đã duyệt hoặc chất lượng từng field Golden.

## Feature flags, API và rollback

Các field request mới là additive, không migration:

```json
{
  "version": 1,
  "integration": true,
  "integration_experiment": "E0",
  "person_recovery": true
}
```

Gửi tới endpoint profile hiện hữu `POST /api/v1/mdm/matching/profiles`. Vẫn cần quyền/feature gates registry, agents và integration, validate profile, rồi chọn default hoặc rebuild rõ ràng bằng flow đã có. `person_recovery` mặc định false; experiment mặc định E3 để giữ request cũ. Không cho recovery ở embedding-only E2. Cấu hình và retrieval version được pin/verify bằng digest; false không thêm key vào config cũ. E0 recovery không gọi encoder và không có Gemini trong benchmark.

Rollback: tạo/chọn profile đã validate với `person_recovery=false` theo registry hiện hữu; run đã pin không thay đổi. Rebuild giữ lịch sử/retained pair nên không xóa candidate đã review để làm rollback; nếu cần universe baseline thuần túy, tạo run mới với profile baseline. Không sửa trực tiếp immutable profile hoặc DB. Thay code matching có thể làm profile cũ không khớp code identity và bị fail-closed theo thiết kế; cần profile mới sau deploy, không cam kết hot-resume artifact của code cũ.

## Tái lập và phạm vi nghiệm thu

```powershell
.venv/Scripts/python.exe -X utf8 scripts/benchmark_person_recovery.py --cases artifacts/full10k-20261016/run-02 --output artifacts/person-recovery-20261016/final-v2 --repeats 3 --score
```

Mỗi case/mode chạy trong process mới, lần lượt. BLAS và rule scoring một worker. Kiểm SHA source trước khi chạy. Truth mở sau toàn bộ production retrieval/scoring/clustering, đọc theo parquet batches. Receipt giữ SHA truth, code fingerprints t?i ??u t?ng trial, config, candidate provenance aggregates, CPU seconds, peak process working set, per-trial wall time và summary. Không dùng embedding cache/provider; OS/file cache không reset. Những lượt probe ban đầu có thể chồng với test; chỉ bảng `verified` dùng làm số đo cuối sau sửa review. N=3 chỉ có median và empirical nearest-rank p95 (thực tế là max mẫu); không đủ kết luận tail latency production.

Artifact `retrieval`, `scoring`, `final`, `verified` là các probe trước sửa resource/rebuild cuối; giữ để đối chiếu, không thay thế receipt `final-v2`. Peak process bao gồm imports, source, provenance, scoring/evaluation và các allocation tạm; không đồng nhất với memory container/backend 1 GiB, Docker VM hoặc toàn host. Chưa chứng nhận no-OOM toàn cửa sổ HTTP, queue latency, provider cost hoặc E2E latency mới.

## Các mục chưa đạt và bước kế tiếp

- Recall retrieval offline tốt không chứng nhận độc lập generalization: đã dùng TC3 để phân tích lỗi; cần split giữ kín/dataset thật độc lập trước khi bật mặc định. Không tuyên bố đây là kết quả held-out sạch.
- Golden precision/recall/F1 tự động qua FINALIZED, field survivorship, chi phí và latency HTTP mới chưa được đo. Auto-merge tiếp tục tắt.
- Chưa benchmark FAISS exact hoặc model đa ngôn ngữ khác. Runtime đã có tiled exact dense; không thêm dependency/encode lại dữ liệu khi rule recovery chưa cần dense. BM25/Lucene, word TF-IDF/ngram 3–5, ANN và meta-blocking chưa được chứng minh cần thiết.
- Chưa sửa scoring floor để nhận các cặp tên rút gọn có DOB nhưng không có anchor đủ mạnh. Cần phân tích review evidence và negative gần giống trên split độc lập, thiết kế rule review phù hợp; không chuyển candidate mới thành auto-merge chỉ vì truth nói đúng.
- Snapshot còn khoảng 264 giây ở nghiệm thu lịch sử. Bước performance tiếp theo là profile SQL/serialization/storage của cùng workload, rồi tối ưu chặng đã đo; không suy tốc độ HTTP từ vài giây retrieval local.
- Cần chạy lại full10k HTTP trên Compose hiện hữu với actor/profile/cache riêng, giữ memory sampler suốt cả hậu kiểm. OOM hậu kiểm lịch sử vẫn là sự cố đã ghi nhận; không xóa artifact hoặc đổi resource limits để che lỗi.

## File thay đổi trong lượt này

| File | Lý do |
| --- | --- |
| `backend/src/masterone/mdm/matching_agents/candidate_union.py` | PERSON recovery, provenance, caps tại engine, workspace/time checks |
| `backend/src/masterone/mdm/matching_agents/integration_profile.py` | Pin flag/version recovery, từ chối E2 |
| `backend/src/masterone/mdm/matching_foundation_contracts.py` | API opt-in recovery và chọn experiment hiện có |
| `backend/src/masterone/mdm/matching_foundation_service.py` | Create/verify immutable config với flags mới |
| `backend/src/masterone/mdm/workflow_candidate_rebuild.py` | Rebuild E0/E1 không gọi encoder/coordinator |
| `backend/tests/test_mdm/test_person_recovery.py` | Unicode/order, mask/type, caps, deterministic, guards, profile/API/worker/rebuild |
| `backend/src/masterone/mdm/pipeline/scoring_engine.py` | Truy?n effective profile v?o process SCORE/DECIDE con |
| `backend/src/masterone/mdm/workflow_service.py`, `pair_read_cache.py` | Status t?i s? d?ng operation metadata, gi? cache/ownership guards |
| `backend/tests/test_mdm/test_generation_staging.py` | Fixture seed ??ng schema migration l?ch s? |
| `backend/tests/test_mdm/test_workflow_audit_fixes.py`, `test_workflow_review_experience.py`, `test_workflow_review_routing.py` | Fixture DB mutation t?ng revision nh? API; kh?ng n?i assertion |
| `backend/tests/test_mdm/test_scoring_profile_workers.py` | Serial/parallel equivalence cho memory/indexed snapshot v? profile b?t/t?t |
| `.gitignore` | Ngo?i l? duy nh?t ?? report y?u c?u xu?t hi?n trong git status |
| `scripts/benchmark_person_recovery.py` | Ablation source-only, truth boundary, metrics/proposals/CPU/RSS, process isolation |
| `docs/ONEMASTER_OPTIMIZATION_REPORT.md` | Audit, bằng chứng, phạm vi và rollback của lượt này |

## M?i tr??ng regression

Full-suite probe ??u v? l??t diagnostic verbose ?? b? d?ng. Trace ghi nh?n c?c test checkpoint/snapshot ngo?i fixture API ??c c?u h?nh R2 t? `.env` local v? timeout trong S3 `exists`/uploader; ??y kh?ng ph?i b?ng ch?ng regression do candidate recovery. Kh?ng d?ng hai l??t ?? l?m PASS, kh?ng x?a/s?a artifact remote. Trace kh?ng ?? ?? k?t lu?n to?n b? c?c request ngo?i process ?? hay ch?a ghi object.

L??t cu?i d?ng `MASTERONE_ENV_FILE` tr? t?i `artifacts/person-recovery-20261016/isolated-test.env` ch? c? comment, kh?ng ch?a credential, v? ch?y c?ng to?n b? backend suite. Env override ch? trong process runner, kh?ng s?a `.env` c?a ng??i d?ng. C?c ki?m th? provider d?ng transport fixture/mock hi?n h?u. L??t benchmark cu?i c?ng d?ng env c? l?p n?y ?? kh?ng ??c credential/c?u h?nh ngo?i ph?p ?o.

## Regression ?? ch?y

| L??t | K?t qu? v? ph?m vi | Evidence |
| --- | --- | --- |
| Full backend, env c? l?p, tr??c s?a c?c failure fixture/status | 1.710 pass, 6 fail, 10 skip; **kh?ng PASS to?n suite** | `artifacts/person-recovery-20261016/backend-tests-isolated.txt` |
| Ch?y l?i 6 failure v?i PYTHONPATH ??ng | 2 memory probes pass; c?n 4 failure ???c gi? ?? debug | `failures-recheck.txt` |
| Sau s?a fixture/schema/revision | 3 pass, c?n 1 SQL query-budget failure th?c s? | `failures-fixed.txt` |
| Sau t?i ?u status/cache | 64 pass | `final-regressions.txt` |
| Code cu?i: recovery, spawned scorer, cache, status, review routing, integration, migration preservation | **71 pass**, 90,16 gi?y | `final-regressions-after-cache.txt` |
| Ruff tr?n to?n b? file Python ch?m trong l??t n?y | **PASS** | `ruff.txt` |
| Full backend cu?i, env/PYTHONPATH c? l?p, sau m?i s?a ??i | **1.716 pass, 10 skip, 0 fail**, 758,07 gi?y | `backend-tests-final.txt` |

Hai file test m?i c? t?ng 16 cases. C? test RED quan s?t tr??c s?a: recovery ch?a ph?t ngu?n m?i; profile ch?a ch?p nh?n flags; budget kh?ng v?o engine; workspace kh?ng ???c ki?m khi kh?ng ph?t pair; rebuild g?i encoder; serial/parallel v2 kh?c evidence. Sau s?a ??u GREEN. Kh?ng d?ng ki?m th? encoder fixture ?? suy accuracy c?a provider. PostgreSQL QA ri?ng v? benchmark opt-in kh?ng ???c c?p m?i tr??ng th? skip, kh?ng g?i l? PASS.

C?c l??t full-suite c? b? d?ng v? c?c l??t fail ???c gi? nguy?n. K?t qu? 71-test l? ki?m tra scope ri?ng; sau ?? ?? ch?y l?i full suite t? ??u v? nh?n 1.716 pass, 10 skip, 0 fail. M??i skip kh?ng ???c di?n gi?i l? PASS cho ki?m th? ch?a th?c thi.

## Benchmark cu?i ? source-only, ba l??t m?i c?u h?nh

Artifact: `artifacts/person-recovery-20261016/final-v2/summary.json` v? 12 receipt ri?ng. T?t c? trial COMPLETE, source/code fingerprints kh?p; c?ng m?i b? c? m?t t?p candidate/recall duy nh?t qua ba l??t. Kh?ng c? provider requests.

| Case / c?u h?nh | Records | Candidates | Recall | Candidate precision | Retrieval median / empirical p95 (s) | Score + guarded cluster median (s) | T?ng production work median (s) | CPU median (s) | Peak process l?n nh?t (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TC1_easy_match / legacy | 10,022 | 83,114 | 100.000% | 5.888% | 4.852 / 4.997 | 13.568 | 18.393 | 17.766 | 1126.8 |
| TC1_easy_match / recovery | 10,022 | 87,078 | 100.000% | 5.620% | 5.227 / 5.624 | 14.635 | 20.217 | 19.172 | 1180.2 |
| TC3_hard_match / legacy | 9,992 | 90,139 | 66.941% | 3.612% | 5.505 / 5.790 | 14.164 | 19.549 | 18.656 | 1243.6 |
| TC3_hard_match / recovery | 9,992 | 93,669 | 100.000% | 5.193% | 5.622 / 5.636 | 15.097 | 20.793 | 19.906 | 1280.4 |

Production work ? b?ng n?y ch? retrieval ? rule score ? guarded proposal; kh?ng g?m source normalization, imports, ??nh gi? truth, HTTP, snapshot/review workflow, Golden publication ho?c FINALIZED. C?c median c?a c?c ch?ng ???c l?y ri?ng, kh?ng c?ng ?? suy median t?ng. Peak memory bao g?m c? normalization/imports/evaluation, kh?c ph?m vi th?i gian trong c?t t?ng.

TC3: th?m 3.530 candidate (+3,916%), t?m th?m 1.608 positive pairs; recall 66,941% ? **100%**. TC1 th?m 3.964 candidate (+4,769%), recall gi? 100%. Kh?ng t?ng cap 200.000. Candidate reduction ratio TC3 recovery v?n kho?ng 99,812% so v?i all-pairs. Kh?ng c? b?ng ch?ng E2E nhanh h?n: total production work median TC1 t?ng kho?ng 1,824 s, TC3 t?ng 1,244 s ?? ??i l?y coverage m?i.

| Rule recovery TC3 | Candidate c?a rule | Candidate m?i sau union | Positive pairs c?a rule | Positive pairs m?i |
| --- | ---: | ---: | ---: | ---: |
| person_token_full_dob | 4,420 | 1,778 | 3,740 | 1,213 |
| person_tokens_month_day | 5,595 | 1,752 | 4,135 | 395 |

Incremental positive ch? t?nh sau retrieval, theo th? t? legacy ? full DOB ? month/day. ??y l? ph?n r? ??ng g?p, kh?ng ph?i c?c th? nghi?m ?i?u ch?nh tham s? theo truth. C?c block recovery ? hai case ??u kh?ng v??t 30; v?n c? test oversized ?? x?c nh?n fail-closed.

### Ch?t l??ng proposal kh?ng truth-assisted replay

| Case / c?u h?nh | Precision | Recall | F1 | Overmerge | Undermerge |
| --- | ---: | ---: | ---: | ---: | ---: |
| TC1_easy_match / legacy | 100.000% | 0.470% | 0.936% | 0 | 4,871 |
| TC1_easy_match / recovery | 100.000% | 0.470% | 0.936% | 0 | 4,871 |
| TC3_hard_match / legacy | N/A (kh?ng c? predicted merges) | 0.000% | 0.000% | 0 | 4,864 |
| TC3_hard_match / recovery | N/A (kh?ng c? predicted merges) | 0.000% | 0.000% | 0 | 4,864 |

C?c ch? s? n?y **kh?ng ph?i Golden FINALIZED**. TC3 kh?ng c? c?m t? ??ng n?n precision kh?ng x?c ??nh; overmerge=0 kh?ng ch?ng minh kh? n?ng ph?n lo?i merge. TC1 ch? 23 positive pairs ???c guarded proposal ch?p nh?n. Kh?ng d?ng recall/F1 th?p c?a policy b?o th? ?? n?i constraints ho?c b?t auto-merge.

TC3 recovery: c? 4.135 PERSON positive pairs ?? v?o candidate nh?ng rule scorer v?n tr? `INSUFFICIENT_EVIDENCE`; c?c positive ???c rule m?i t?m c? reason `NO_VALID_IDENTITY_ANCHOR`. 729 ORGANIZATION positive pairs b? person rule ??nh gi? `NOT_MERGEABLE`/out-of-scope; kh?ng c?p quy?n merge m?i. V? v?y retrieval bottleneck ?? ???c g? trong diagnostic n?y, nh?ng identity evidence/eligibility v? workflow duy?t v?n l? n?t th?t ch?t l??ng.

B?o to?n record trong diagnostic: ID source/truth tr?ng universe, kh?ng duplicate ID, c?c proposal kh?ng ch?ng membership, ph?n c?n l?i ???c h?ch to?n singleton. S? record l?n l??t 10.022/9.992. Ch?a thay cho h?u ki?m frozen membership c?a HTTP.

Peak process c?a recovery kho?ng 1,15 GiB TC1 v? 1,25 GiB TC3; ch?a gi?m memory so v?i baseline. Detailed decision payload v? evaluation v?n chi?m RAM. Kh?ng ch?y c?ng c? n?y trong backend 1 GiB v? kh?ng coi estimate workspace recovery (~77/56 MiB) l? RSS to?n process. Kh?ng c? OOM trong 12 child trials offline; no-OOM to?n stack/VM v? c?a s? h?u ki?m HTTP v?n ch?a ch?ng nh?n.

### Acceptance gates c?a l??t n?y

| Gate | Tr?ng th?i |
| --- | --- |
| TC3 candidate recall ?90% | PASS diagnostic offline, 100% ? 3; ch?a ch?ng nh?n HTTP/held-out ??c l?p |
| Kh?ng regression candidate recall TC1 | PASS diagnostic offline, 100% ? 3 |
| Candidate explosion | Kh?ng x?y ra trong hai case, c? hai d??i cap 200.000 |
| Hard constraints/tenant/ORGANIZATION | Guards gi? nguy?n; regression li?n quan pass; kh?ng c? quy?n m?i |
| Golden quality t? ??ng t?ng | CH?A ??T; proposal quality kh?ng t?ng, ch?a ?o Golden HTTP m?i |
| Zero overmerge | 0 ? guarded proposals; TC3 kh?ng predicted merge n?n kh?ng suy ch?t l??ng production |
| No-OOM v? record conservation to?n workflow | CH?A CH?NG NH?N m?i; offline b?o to?n source/proposals, kh?ng OOM trong child trials |
| E2E latency v? provider quality/cost m?i | CH?A ?O; offline 0 provider attempts, embedding cache hit rate N/A |
| Existing backend regression | PASS cho 1.716 test ?? ch?y ? l??t cu?i; 10 skip ch?a ???c ch?ng nh?n |
| Default activation / auto-merge | Kh?ng thay ??i; recovery false m?c ??nh, auto-merge v?n t?t |
