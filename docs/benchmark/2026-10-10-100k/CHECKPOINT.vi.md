# Checkpoint benchmark — tạm dừng theo yêu cầu người dùng

Ngày: 10/10/2026. Lý do: người dùng sắp hết quota và yêu cầu lưu checkpoint để tiếp tục lần sau. Đã dừng driver, controller, worker, API và monitor riêng của benchmark; xác nhận không còn tiến trình benchmark chạy. Docker và các dịch vụ khác được giữ nguyên.

## Phạm vi cần tiếp tục

Benchmark production thực tế trên toàn bộ file của tám bộ TC `100k/seed-7` tại `C:\VinUni\Dự án nhóm\GenVN\benchmark_datasets`, có Gemini thật. Không chạy 1M. Đo từ upload HTTPS, R2 thật, validation/commit/intake PostgreSQL, queue/lease/heartbeat đến pipeline và golden record, dừng ngay trước `persist_snapshot` ghi kết quả. Intake, queue và LLM accounting có ghi DB riêng; các bảng kết quả pairs/proposals/review cases phải bằng 0. Không coi đây là benchmark tốc độ ghi/publish, UI, HA hoặc concurrency.

Workspace: `C:\VinUni\Dự án nhóm\P-119`. Git commit nền: `4cb00d5870a99a1a56bac6360af3b65bc2cfa8e7`. Không sửa core ứng dụng hoặc `.env`. Các script benchmark mới và artifact chưa commit; cần giữ cả workspace, `.venv`, DB riêng và object R2.

## Kết quả đã hoàn tất và được giữ

| Bộ | Profile | E2E upload → trước persist, giây | Ghi chú |
|---|---|---:|---|
| TC13_HOT_BLOCK_RETRIEVAL | Baseline tắt LLM | 609.24 | Hoàn tất |
| TC1_easy_match | Gemini, output cap 512 | 797.53 | Không có pair đủ điều kiện LLM |
| TC2_easy_non_match | Gemini, output cap 512 | 585.80 | Không có pair đủ điều kiện LLM |
| TC3_hard_match | Gemini, output cap 512 | 563.09 | Không có pair đủ điều kiện LLM |
| TC4_hard_non_match | Gemini, output cap 512 | 603.17 | Không có pair đủ điều kiện LLM |
| TC5_real_world | Gemini, output cap 512 | 628.50 | Chỉ retry phần hợp lệ 98.886 record |
| TC6_CLUSTER_BRIDGE | Gemini, output cap 512 | 834.39 | Ba request thực tế lỗi SCHEMA_INVALID |
| TC8_JUNK_PLACEHOLDER | Gemini, output cap 512 | 715.90 | Ba request thực tế lỗi SCHEMA_INVALID |
| TC13_HOT_BLOCK_RETRIEVAL | Gemini, output cap 1024 | 1175.65 | 10 request SUCCESS, 100 pair được annotate |

Tổng chín lượt hoàn tất: tám kết quả tại `artifacts/production-benchmark/20261010-100k/results.json`, một kết quả tại `.../llm-corrected/results.json`. Các lượt hoàn tất đều chạm boundary trước persist, kiểm tra ban đầu không có dòng kết quả PostgreSQL. Đây là PASS về thực thi boundary, không đồng nghĩa chất lượng đạt production. Verifier độc lập và báo cáo cuối chưa chạy.

Những phát hiện sơ bộ cần đưa vào báo cáo và đối soát:

- Cả ba key Gemini đã kiểm tra thành công qua provider và policy của ứng dụng. Runtime chọn một key, không có pool/rotation ba key.
- TC13 với cap 1024 đã thực sự dùng Gemini trên pipeline 100K; 100 annotation DIFFERENT, không làm đổi rule baseline/auto permission. Candidate recall khoảng 99,976%; auto precision trên CERTAIN bằng 1, recall khoảng 90,263%.
- TC3 candidate recall khoảng 57,529%; toàn bộ positive được retrieve đi nhánh INSUFFICIENT, không auto merge.
- TC5 toàn bộ nguồn có 99.407 record; 105 lỗi decode và 416 lỗi mandatory bị loại, còn 98.886 record. Đã thử upload đầy đủ và nhận validation ERROR trước retry. Có 14 JUNK bị merge: phải nêu rõ dù precision trên CERTAIN bằng 1. Có 30 lỗi LLM nhưng không request outbound; chưa xác minh nguyên nhân PolicyDenied, không kết luận INPUT_CAP cho đến khi replay.
- Cap 512 thất bại với batch ID SHA256 đầy đủ; probe cap 1024 thành công. TC6/TC8 cap 512 vẫn giữ là kết quả thực tế, không thay thế hoặc che lỗi.
- Network Gemini khoảng 2 giây nhưng batch wall TC6 cap 512 khoảng 75–79 giây: policy snapshot/hash/accounting trên toàn bộ 100K là bottleneck cần đối soát.
- R2 endpoint trong `.env` có thêm bucket path gây HeadBucket 404. Benchmark override riêng bỏ path thành công; chưa sửa `.env`.
- Các flag v2/streaming/resume/staging vẫn tắt; đang đo legacy large-data path thực tế. Projection legacy/Excel diễn ra ngoài đồng hồ E2E, không phải native Excel ingestion.

## Lượt bị ngắt

TC6 cap 1024 tại `.../llm-corrected/TC6_CLUSTER_BRIDGE`, run ID `90fbb69a-de72-4e7e-b8da-f3961e3f1e6e`. PREPARE, NORMALIZE, BUILD_CANDIDATES và SCORE đã xong khi dừng; chưa có kết quả hoàn tất. Giữ stage artifact/log để điều tra, không dùng làm latency hoặc quality cuối, không tính là lỗi production. Chi phí LLM của lượt dở cần đối soát riêng.

Trạng thái machine-readable: `artifacts/production-benchmark/20261010-100k/checkpoint_pause.json`; marker tại case `paused_by_user.json`. Intake và queue DB cũ giữ nguyên, lease có thể hết hạn. Chạy tiếp bằng DB mới, không claim lại queue cũ. Monitor có thể chưa flush toàn bộ mẫu cuối do bị dừng; không dùng mẫu thiếu làm số liệu hoàn chỉnh.

## Lệnh tiếp tục

Chạy PowerShell tại workspace. Đầu tiên đọc checkpoint này và kiểm tra Docker, R2/Gemini qua cấu hình hiện có mà không in secret. TC6 resume root đã chuẩn bị sẵn inventory một case, lineage và profile cap 1024; chưa chạy.

**Không chạy lại `benchmark_finalize_suite.py` từ đầu. Không chạy `production_benchmark.py run` vào root có `results.json`: runner tạo DB mới và ghi đè danh sách kết quả.** Nếu lần resume tiếp theo lại bị ngắt, tạo một root mới cho lượt đó.

```powershell
$benchRoot = 'artifacts\production-benchmark\20261010-100k'
.venv\Scripts\python.exe -X utf8 scripts\production_benchmark.py run --output "$benchRoot\llm-corrected-tc6-resume"
.venv\Scripts\python.exe -X utf8 scripts\production_benchmark.py run --output "$benchRoot\llm-smoke-512"
.venv\Scripts\python.exe -X utf8 scripts\production_benchmark.py run --output "$benchRoot\llm-smoke-1024"
```

Hai smoke đã chuẩn bị, mỗi bộ 10.040 record để đi qua StageRunner lớn; chỉ là test nhánh địa chỉ/schema LLM, không thay thế tám bộ TC hoặc dùng để tuyên bố độ chính xác nghiệp vụ.

Sau các lượt timed, chạy đối soát offline, không gửi thêm Gemini:

```powershell
$evidenceRoots = @($benchRoot, "$benchRoot\llm-corrected", "$benchRoot\llm-corrected-tc6-resume", "$benchRoot\llm-smoke-512", "$benchRoot\llm-smoke-1024")
foreach ($evidenceRoot in $evidenceRoots) {
    .venv\Scripts\python.exe -X utf8 scripts\benchmark_verify_evidence.py --input $evidenceRoot
    .venv\Scripts\python.exe -X utf8 scripts\benchmark_analyze_llm_eligibility.py --input $evidenceRoot
}
```

Verifier có thể exit 0 dù có check FAIL: phải đọc từng check trong `verified_evidence.json`. Verifier và helper `benchmark_field_reference.py` chưa từng chạy; sửa lỗi harness nếu phát hiện, không đổi core hay kết quả nguồn. Field agreement dùng canonical truth; golden replay dùng chính survivorship trên normalized artifact, chỉ là deterministic replay, không phải gold độc lập hay kiểm tra payload đã publish. TC5 payload replay là bước xác minh lỗi policy, chưa được làm.

Tiếp theo chạy backend `scripts/run_merge_baseline.py --suite all --timeout 1200` và frontend Vitest, lưu evidence và tổng hợp `regression_results.json` theo phần cuối của `scripts/benchmark_finalize_suite.py` (chỉ tái sử dụng phần verification/regression/report, bỏ phần chạy lại case và PID cũ). Thu dependencies cuối và hash script. Không báo PASS cho test chưa chạy.

Sinh báo cáo cuối sau khi đối soát xong:

```powershell
.venv\Scripts\python.exe -X utf8 scripts\production_benchmark_report.py --input $benchRoot --extra "$benchRoot\llm-corrected" --extra "$benchRoot\llm-corrected-tc6-resume" --extra "$benchRoot\llm-smoke-512" --extra "$benchRoot\llm-smoke-1024" --output docs\benchmark\2026-10-10-100k
```

Báo cáo dự kiến: `REPORT.vi.md`, `metrics.csv`, `summary.json`, biểu đồ stage/quality. Kiểm tra bảng và biểu đồ, phân biệt baseline/512/1024; không xem các lượt với UUID khác là controlled causal A/B. `draft-report` hiện cũ, chỉ có baseline; **không giao như báo cáo đầy đủ**.

## Evidence và cấu hình cần giữ

- Root evidence: `artifacts/production-benchmark/20261010-100k` (git-ignored), gồm inventory/hash nguồn, projection/lineage, upload/validation/commit/queue, Parquet stage, pair decisions, cluster ownership, resource samples, provider probes.
- PG16: container `masterone-g7-database-1`, host port 15437. DB nền `benchmark_20261010_104617_fa48a7`; DB corrected đọc tên tại `llm-corrected/environment.json`. Không migrate/delete DB khác.
- Live Gemini model `gemini-3.5-flash-lite`, tối đa 100 semantic pairs/run, batch 10; profile cap 1024 ở `llm_profile.json`. Chỉ dùng dữ liệu synthetic L0. Contract identifier biểu thị review public terms cho synthetic, không phải chứng minh tài khoản paid/zero retention. Giá policy 3 microUSD/token là upper bound, không phải hóa đơn thực tế; tách request thiếu usage và lượt ngắt khi báo chi phí.
- `private_state` có TLS private key test; không đưa vào gói chia sẻ. Không xuất `.env` hay API key vào báo cáo.
- Chín script mới: `production_benchmark.py`, `production_benchmark_report.py`, `benchmark_llm_gateway.py`, `benchmark_service_monitor.py`, `benchmark_verify_evidence.py`, `benchmark_prepare_llm_smoke.py`, `benchmark_analyze_llm_eligibility.py`, `benchmark_finalize_suite.py`, `benchmark_field_reference.py`. Ruff đã pass trước checkpoint; regression chưa chạy. `.venv` có bổ sung cryptography và matplotlib.

Prompt lần sau: **“Tiếp tục benchmark theo docs/benchmark/2026-10-10-100k/CHECKPOINT.vi.md; giữ các kết quả đã hoàn tất, chạy nốt TC6 cap 1024 và các bước còn lại, không chạy 1M.”**
