# Production readiness — 2026-10-09

**Decision: NOT READY.** Có execution thật và nhiều test PASS, nhưng không đủ bằng chứng/quality để xác nhận full system production-ready. Không tự deploy hoặc bật experimental flags trên production.

| Gate | Trạng thái | Bằng chứng / điều kiện còn thiếu |
|---|---|---|
| A Environment | PASS, local QA scope | Images build; migration head0025;13 changed module hashes khớp image; PG/API/Next healthy. Worker/Moto functional nhưng không có healthcheck. |
| B Storage | BLOCKED cho full scope | Local PG/recovery và real Neon/R2 bounded tests PASS; cloud independent worker/recovery, complete orphan reconciliation/durability chưa đủ bằng chứng. |
| C Pipeline | FAIL | 100/1k/4k và TC1–4 10k completed; cross-batch fixture/constraints/golden proofs PASS. TC3 recall66,94%, TC5 recall91,48% và native TC5 ingestion422. |
| D Frontend | PASS cho active synthetic workflow | Real production Next + Edge + API/worker: review/golden/merge/CSV/history/split/audit; Viewer UI/API denied. Không chứng nhận four-eyes, native direct R2 UI hoặc full accessibility audit. |
| E Performance | FAIL full readiness | Baseline và stage metrics có; legacy guard bottleneck đã sửa không đổi hash. V2 10k ~474,5s và candidate recalls chưa đạt yêu cầu đủ tin cậy; sustained load/leak soak chưa chạy. |
| F Operations | BLOCKED | Real killed-child/retry, claims/fencing, DB/storage interruption PASS; runbook/logging/readiness có. Metrics/alerts, worker healthcheck, reconciliation và cloud deployment drills chưa hoàn tất. |
| G Regression/required checks | FAIL | Executed tests: backend full884 PASS/1 SKIP; explanation targeted43 PASS sau sửa; frontend50; mocked E2E10; real integration/browser artifacts riêng. Existing CI-required broad Ruff fails210findings; remote CI NOT RUN. |
| H LLM integration | PASS cho legacy Gemini canary; BLOCKED cho toàn bộ providers | Helpers và actual API/cache có real canary PASS, một schemaFAIL đã sửa. 4/4 requests đã dùng, no retry. OpenRouter/new router unwired; deep LLM audits NOT RUN. |

PASS có phạm vi ở bảng không mở rộng thành PASS toàn phase hoặc certification production. GateB/F BLOCKED là thiếu execution/implementation evidence, **không phải thiếu credentials hiện tại**.

## P0/P1/P2

- Các lỗi nguy cơ startup, wrong migration target, silent missing storage input và NULL serving adapter được sửa và regression thực thi. Chưa phát hiện thêm P0 tái hiện còn mở trong phạm vi này; không chứng minh toàn hệ thống không còn P0.
- P1 còn mở: candidate recall hard cases; native TC5 source/name/format support; v2 checkpoint/streaming throughput; GroupD governance decision; orphan reconciliation/worker health/metrics; full cloud worker/recovery/TLS validation; legacy/new LLM flag/budget boundaries; reproducible dependency lock.
- P2: stale architecture/run docs, broader Ruff cleanup, complete administrative registry coverage và expanded UI/accessibility/soak measurements.

## Điều kiện để chuyển trạng thái

1. Chốt recall/precision targets và product support cho TC5; chạy shadow benchmark với ground truth độc lập. Không thay labels hay bỏ guards.
2. Làm rõ GroupD approval contract, LLM flags/routing và cost ownership trước audit security tiếp theo.
3. Giải quyết v2 performance theo profiling có checkpoint correctness tests; hoặc pin legacy rõ ràng cho audit/demo và báo giới hạn.
4. Kiểm chứng cloud independent worker/recovery/TLS và reconciliation bằng QA namespace; hoàn tất monitoring/health gates.
5. Chạy lại regression/E2E/benchmarks sau các sửa liên quan, cập nhật machine results và GateA–H.

Có thể dùng môi trường hiện tại để khảo sát LLM smoke/security findings theo phạm vi giới hạn, nhưng **không ghi READY FOR NEXT AUDIT cho full-system prerequisite**. Bảy audit không được mặc định mọi route/provider/cost guard đã nối runtime.

Xem [FULL_SYSTEM_AUDIT](FULL_SYSTEM_AUDIT.md), [TEST_MATRIX](TEST_MATRIX.md), [PERFORMANCE_BENCHMARK](PERFORMANCE_BENCHMARK.md), [INFRA_VALIDATION](INFRA_VALIDATION.md), [RUNBOOK](RUNBOOK.md). Machine artifacts ở `artifacts/full-system-20261009/`; không có secrets/raw PII trong các báo cáo Markdown.
