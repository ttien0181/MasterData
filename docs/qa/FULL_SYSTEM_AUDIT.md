# MasterOne — full system audit, 2026-10-09

## Kết luận và phạm vi

**NOT READY** cho việc xác nhận full-system production readiness. Hệ thống đã chạy thực tế, đã sửa các lỗi integration và có bằng chứng local Docker, Neon/R2 thật, browser thật và Gemini. Tuy nhiên, candidate recall của hard-match còn thấp, TC5 chưa qua native ingestion, và một số luồng được mô tả trong tài liệu chưa nối vào runtime. Đây là readiness assessment, không thay thế bảy audit LLM chuyên sâu.

Đã tiếp tục checkpoint ngày 2026-10-09, không chạy lại discovery từ đầu. Branch `qa/full-system-20261009`, base `b45142a`; chưa commit/stage. Bảo toàn các thay đổi và artifact sẵn có của người dùng. Chỉ dùng năm dataset `TC*/10k/seed-7` của GenVN. **21k/100k/1M NOT RUN theo chỉ dẫn 10k-only**. Không deploy, reset dữ liệu production hoặc gửi dataset benchmark đến LLM.

Kết quả, lệnh và giới hạn nằm trong [TEST_MATRIX](TEST_MATRIX.md), [PERFORMANCE_BENCHMARK](PERFORMANCE_BENCHMARK.md), [INFRA_VALIDATION](INFRA_VALIDATION.md), [PRODUCTION_READINESS](PRODUCTION_READINESS.md) và [RUNBOOK](RUNBOOK.md). Checkpoint cũ là bằng chứng lịch sử; báo cáo này thay thế nhận định tại lúc pause.

## Kiến trúc được kiểm tra

Next.js ở `frontend/app` và `frontend/src`; FastAPI package ở `backend/src/masterone`. Frontend production Docker dùng build standalone. PostgreSQL được chọn bằng `MDM_DATABASE_PROFILE` và `MDM_DATABASE_URL[_LOCAL]`; Neon là PostgreSQL staging thật. Queue và worker độc lập dùng PostgreSQL leases/claims, không cần Redis cho resolution pipeline.

Luồng đang nối: browser → upload-session HTTP chunks → backend staging/parse CSV → PostgreSQL và S3 source artifacts → worker → normalization/global candidates/scoring/constraints/grouping/clustering → serving snapshot → steward review → preview/execute → CSV/R2, merge events và audit → selective split tạo export mới. **Không phải luồng browser direct-to-R2 upload**. Router `large_imports` cho luồng lớn/object references và router mới `mdm_explanations` chưa được mount trong `main.py`.

Runtime <=10.000 records dùng `_compute`; >10.000 dùng `StageRunner` DuckDB/Parquet. V2 selection, generation staging, checkpoints và streaming có code và đã chạy opt-in QA; selection được pin theo run. V2 khác legacy về semantics, không bật trên production. Candidate index/union vẫn global trong RAM; legacy cluster còn dùng RAM. Không khẳng định toàn bộ pipeline memory-bounded.

Đã đọc README/architecture/env/Compose/Docker/package metadata, tài liệu `docs/merge` về contracts, decisions, candidates, workers, generations, streaming/resume và các module/test tương ứng. `docs/merge/CONTRACTS.md` có một số trạng thái lịch sử đã cũ: hiện schema head là **0025_pipeline_checkpoints**, production startup chỉ validate schema. Không khẳng định đã kiểm toán từng dòng của toàn repository.

## Component inventory

Quy ước đếm: 25 capability/service dưới đây; **21 OPERABLE (có giới hạn), 2 GAP/FAIL, 2 UNWIRED**. OPERABLE chỉ xác nhận có execution trong phạm vi ghi rõ, không đồng nghĩa mọi yêu cầu production đều PASS.

| # | Component / nơi chính | Runtime và execution | Test/config/giới hạn | Trạng thái |
|---|---|---|---|---|
| 1 | Next frontend, `frontend/` | Production build, real Edge login/upload/review/export/split | Vitest 50; mocked E2E 10; real browser riêng | OPERABLE |
| 2 | FastAPI, `main.py`, `api/` | HTTP health/ready và app contracts | Full backend suite; production lifespan riêng | OPERABLE |
| 3 | Auth/RBAC/CSRF, `auth/` | SENIOR_STEWARD và VIEWER, ownership 404, mutation 403 | Explicit capabilities; không bypass login | OPERABLE |
| 4 | Local PostgreSQL, `db/session.py` | Read/write, constraints, transaction/deadlock/pool/restart | QA DB riêng; migrations đã chạy | OPERABLE |
| 5 | Neon PostgreSQL profile | Real migration + API/pipeline/export 100 + bounded pool concurrency | Chưa deploy cloud worker/TLS ingress | OPERABLE |
| 6 | R2 adapter, `storage/` | Real multipart, range, Parquet, checksums, concurrency/abort | Owned QA prefix; SDK timeouts/retries bounded | OPERABLE |
| 7 | PG queue/worker, `mdm/job_queue.py`, `resolution_worker.py` | Two workers, 12 claim contenders, killed child/retry/fencing | Không có worker healthcheck; crash test local | OPERABLE |
| 8 | CSV ingestion/upload sessions | Real 100/1k/4k/10k, invalid imports và simultaneous uploads | UTF-8 CSV, CRM/ERP/BILL/CSV, <=10 files; TC5 thiếu support | OPERABLE |
| 9 | Vietnamese standardization, `pipeline/standardization.py` | Names/phones/email/DOB/GovID/address, raw giữ nguyên | Calendar bug đã sửa; không có complete old/new administrative registry | OPERABLE |
| 10 | Data quality/provenance | Normalized/raw/quality/lineage trong DB; invalid inputs thấy lỗi | Unit và count/provenance assertions | OPERABLE |
| 11 | Candidate generation/blocking, `candidate_engine.py` | Global cross-batch fixture PASS; TC3/TC5 execution hoàn tất | Recall TC3 66,94%, TC5 91,48%; còn misses | FAIL |
| 12 | Pair scoring/hard constraints | Rule features, typed constraints, strict serving adapter | Backend boundary/conflict/missing tests; score không phải probability | OPERABLE |
| 13 | A/B/C/D + subgroups | Có classification và precedence đang dùng | B thường biểu diễn normalization tags/signatures; không coi mọi tên nhóm trong đề bài là enum runtime | OPERABLE |
| 14 | Auto-merge policy | Eligibility/guard enforcement + synthetic AUTO proposal thật | Không auto-approve benchmark truth; observed false auto merges 0 chỉ trên CERTAIN subset | OPERABLE |
| 15 | Constrained clustering/guards | Anti-transitive/negative/shared/source/size checks | Indexed-vs-full regression + exact output hashes | OPERABLE |
| 16 | Golden/survivorship/lineage | Browser source selection được DB merge events và CSV giữ lại | 7 source records, 2 initial merges, 6 traced golden values | OPERABLE |
| 17 | Manual review/approval | Real reject/approve tạo decisions/proposal/audit | Viewer denied; stale pair rejected | OPERABLE |
| 18 | Group D four-eyes | Legacy case code/schema có; active pair/item flow khác | Contracts đã ghi nhận một steward có thể approve D; cần product decision | GAP |
| 19 | Audit/history/selective rollback | Real split, immutable parent export, audit, replay | Selective unmerge; không khẳng định arbitrary database rollback | OPERABLE |
| 20 | CSV export/download | Real source coverage, checksum, replay, missing-object 409 | Bounded export index/budgets; external IDs trong CSV, UUID trong event lineage | OPERABLE |
| 21 | Legacy direct Gemini | Real semantic/explanation helpers và actual explanation API/cache | 4 canaries tổng cộng, request 3 FAIL rồi request 4 PASS; no retries | OPERABLE |
| 22 | New OpenRouter LLM/cache/rate/cost subsystem | Code và mocked tests có, router chưa main-mounted | User chọn Gemini; OpenRouter key absent, real OR NOT RUN | UNWIRED |
| 23 | V2 generation/resume/streaming | Real 100/1k and TC1 10k opt-in completed, publication/checkpoints | 10k ~474,5s, khác legacy policy; hiện reset flags OFF | OPERABLE |
| 24 | Readiness/structured logging | Dependency outages 503; liveness 200; safe worker/exception logs | Chưa có đầy đủ stage/queue metrics, dashboards, health alerts/reconciliation daemon | OPERABLE |
| 25 | Native large imports/direct object-reference UI | Code riêng tồn tại, main/UI chưa wire | Native XLSX/CP1258/mixed TC5 upload chưa hỗ trợ đầy đủ | UNWIRED |

## Lỗi và bản sửa

| Vấn đề / nguyên nhân gốc | Sửa ở | Bằng chứng sau sửa |
|---|---|---|
| Docker context kéo theo dependencies/artifacts; frontend runtime/build không khớp Node requirement | Dockerfiles, dockerignore, QA Compose; Node24 đã tương thích installed dependencies | Real images build + production frontend; final runtime fingerprint |
| Programmatic migration target bị ambient env ưu tiên, có nguy cơ nhắm DB khác | `backend/migrations/env.py`, `mdm/migrate.py` | Explicit-target regression giữ DB unrelated untouched; real QA schema head |
| Production lifespan auto-migrate; thiếu dependency readiness | `main.py`, `mdm/migrate.py` | Read-only production validation; DB/storage outages và production HTTPS TestClient |
| Stale DB pool sau restart, connect chưa bounded | `db/session.py` | Pre-ping/recycle300/connect5; actual DB restart và bounded Neon pool |
| Ingestion nuốt R2 upload error và có thể commit input thiếu artifacts | `resolution_import.py` | HTTP502 + transaction rollback; multi-file owned compensation regression |
| Source fixture thiếu external identity, normalization errors bị nuốt | `stage_runner.py` | Source-neutral defaults cho direct caller; explicit identities preserved, failures visible |
| Pandas NULL→NaN khiến strict adapter `ENGINE_GROUP` reject TC1/TC4 sau compute | `stage_runner.py` đọc Arrow thay Pandas | TC1/TC4 live PASS, null/hard-conflict serving regression; không nới adapter |
| Default StageRunner dùng chung scratch của attempts | `stage_runner.py` dùng unique `mkdtemp` | Two-attempt constructor regression; baseline plugin wrapper có `__wrapped__` để test defaults thật |
| Cluster guards scan ALL_PAIRS và đọc YAML mỗi union | `clustering.py`, `cluster_guards.py` | Exact decision/cluster hashes giữ nguyên; TC1 cluster60,171→1,687s; RAM tradeoff |
| R2 StreamingBody không đóng khi read error; signing endpoint nội bộ không browser reachable | `storage/object_store.py`, `storage/r2.py`, config | Body-close tests; real range/checksum; local browser signed downloads |
| Download presign trả redirect dù output mất | `api/mdm_resolution.py` | Missing export409, SDK availability503; owned object restore + checksum |
| Lazy log args lộ PII; exception text có thể chứa SQL params/secrets | `logging_config.py`, lifecycle/worker | Render-before-mask và safe error type/frames tests; không coi đây là full privacy audit |
| Gemini numbered keys không được đọc; output/retry caps thiếu | `config.py`, Gemini helpers | All3 authentication GETs; first-nonempty selection; caps và actual canaries |
| DOB31/04,30/02 và leap-year sai vẫn VALID | `standardization.py` calendar `date()` check | 8 calendar regressions; five datasets candidate counts/recall preserved |
| Explanation response schema không constrain accepted field vocabulary | `gemini_explanation.py` thêm enum/short-summary guidance | Mock request-schema regression; actual API request3 schema FAIL, request4/cache PASS |

Không sửa matching thresholds, ground truth, cluster guards hoặc đổi source Billing/DW thành nguồn khác để làm metric đẹp. Các lỗi test harness ban đầu (CORS allowlist, missing asyncio plugin, shared scratch override, browser accessibility locator, required golden choice và UUID-vs-external-ID assumption) được giữ trong artifacts và tách khỏi lỗi implementation.

## Remaining issues / proposals

**P1 — Candidate recall:** TC3 thiếu1608/4864 CERTAIN pairs, TC5 thiếu902/10582. Không phải chỉ vì ingestion chunk boundary: synthetic batch1/5/8 PASS, nhưng partitioned retrieval/oversized buckets/Top-K/quality filtering vẫn bỏ sót. Probe union với global sparse giữ toàn bộ legacy candidates, nâng recall76,15%/95,87% nhưng vẫn thiếu1160/437 pairs và tăng candidates. Chưa bật default. Cần bounded global retrieval + oversized-bucket secondary keys, đo recall/census/memory và shadow outputs trước policy activation; không bỏ caps hoặc chạy all-pairs mù quáng.

**P1 — TC5 ingestion:** Native API422 vì source contract, required names và native formats. QA adapter decode được10010 accepted,9 rejected có báo cáo; 30 accepted rows thiếu name. Adapter/offline computation không phải native E2E PASS. Cần product/source contract và native decoder/quarantine design; không invent names/IDs, drop failed rows hoặc sửa labels.

**P1 — V2 performance/semantics:** TC1 opt-in engine/streaming/resume completed nhưng ~474,5s,23 proposals so với legacy2190. Policies/constraints khác nên không so hash hay coi giảm proposals là optimization. Checkpoint/partition ledger throughput là bottleneck được quan sát; cần profile và tối ưu metadata/verification mà không phá immutable/pinned/checksum rules.

**P1 — Workflow/governance:** Active GroupD approval không dùng legacy four-eyes contract. Owner-only access cũng cần thiết kế quyền review thứ hai nếu chọn four-eyes. Không tự đổi nghiệp vụ đã ghi rõ trong `docs/merge/CONTRACTS.md` chỉ để test pass.

**P1 — Storage/operations:** Compensation và missing-output detection có, nhưng chưa có durable reconciliation/orphan audit service bao phủ mọi crash point. Moto restart durability chưa được chứng nhận. Worker/storage thiếu healthcheck và alert. Real deployed Neon worker, cloud failure injection, TLS ingress và sustained load/leak soak NOT RUN.

**P1 — LLM runtime boundaries:** Active legacy Gemini dùng key/on-demand và `SEMANTIC_ADDRESS_ENABLED`; new `LLM_*` flags/limits là subsystem riêng chưa wired. Không khẳng định global flags/cost ledger áp dụng cho mọi legacy route. Ba Gemini keys được đọc nhưng không rotation. Central dollar budget enforcement, injection/privacy/reproducibility/gray-zone quality thuộc audit tiếp theo, chưa PASS.

**P2 / CI gate:** Backend dependencies có lower bounds, host/container versions khác, chưa có release lock; tài liệu cũ cần đồng bộ. Broad CI-required Ruff fails210style/import findings; remote CI chưa chạy. Không unsafe-autofix Arrow variables được DuckDB dùng động. Address abbreviations có nhưng complete versioned administrative mapping chưa có bằng chứng. Regression tests PASS không làm CI lint gate thành PASS.

Provider schema tham khảo official [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output?hl=en), có string enum support. Kết quả canary trong báo cáo là execution evidence riêng, không suy ra từ tài liệu provider.

Không còn startup/data-loss regression đã tái hiện nào trong phạm vi sửa nhỏ được để mở. Không có bằng chứng để kết luận không còn P0 trên toàn hệ thống. Các P1 trên chặn full-system readiness; một số cần thiết kế/product decision thay vì architectural rewrite trong audit này.
