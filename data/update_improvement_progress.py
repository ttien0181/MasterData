import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def write(path, value):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value, encoding='utf-8')

db = sqlite3.connect(ROOT / 'data/live_ai_validation.db')
db.row_factory = sqlite3.Row
run = dict(db.execute('SELECT * FROM agent_runs WHERE run_id=?', ('agent-4559b66792e149d994b9c83d821b7ce3',)).fetchone())
tools = [dict(r) for r in db.execute('SELECT tool_name,status,outputs_json FROM tool_calls WHERE run_id=? ORDER BY created_at', (run['run_id'],))]
proof = {k: run[k] for k in ('run_id','proposal_id','revision','model_version','prompt_version','status','error_code')}
proof['result'] = json.loads(run['result_json'])
proof['usage'] = json.loads(run['usage_json'])
proof['tools'] = [{'tool_name': t['tool_name'], 'status': t['status'], 'output': json.loads(t['outputs_json'])} for t in tools]
proof['easy_case'] = {'status':'NOT_NEEDED','llm_calls':0}
proof['scope'] = 'One synthetic live case; not an independently labeled quality evaluation. Configured price estimate, not invoice.'
write('eval/results/resolver_validation/live_demo.json', json.dumps(proof,ensure_ascii=False,indent=2))
db.close()

groups = ['scale_1000','scale_5000_budget250k','scale_10000_budget250k','scale_21k_v3']
rows = []
for group in groups:
    runs = []
    for d in sorted((ROOT / 'eval/results' / group).glob('eval-*')):
        if not (d / 'metrics.json').exists(): continue
        metrics = json.loads((d/'metrics.json').read_text(encoding='utf-8'))
        metadata = json.loads((d/'metadata.json').read_text(encoding='utf-8'))
        warmup = metadata.get('warmup',False)
        runs.append({'run_id':d.name,'warmup':warmup,'runtime_seconds':metrics['runtime_total'],'rss_bytes':metrics['resources']['process_rss_peak_sampled_bytes'],'counts':metrics['counts'],'blocking_recall':metrics['blocking_recall'],'cluster_f1':metrics['cluster_pairs']['f1'],'false_positive_links':metrics['false_positive_cluster_links']})
    measured=[r for r in runs if not r['warmup']]
    if measured:
        vals=[r['runtime_seconds'] for r in measured]
        rows.append({'group':group,'runs':runs,'measured_runs':len(measured),'median_seconds':statistics.median(vals),'min_seconds':min(vals),'max_seconds':max(vals),'rss_peak_mib':max(r['rss_bytes'] for r in measured)/1048576})
write('eval/results/scale_summary.json',json.dumps(rows,indent=2))
table='| Slice/config | Measured runs | Core seconds median (min–max) | Peak RSS MiB | Blocking recall | Cluster F1 | FP links |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n'
for r in rows:
    last=r['runs'][-1]
    table+=f"| {r['group']} | {r['measured_runs']} | {r['median_seconds']:.2f} ({r['min_seconds']:.2f}–{r['max_seconds']:.2f}) | {r['rss_peak_mib']:.0f} | {last['blocking_recall']:.5f} | {last['cluster_f1']:.5f} | {last['false_positive_links']} |\n"
write('docs/performance_21k.md', '''# Performance validation — 2026-09-27

Dataset: newly generated synthetic vn-synthetic-v1, seed 119, 21,000 records / 6,000 entities, CRM/ERP/Billing 7,000 each. This is not the missing MHT dataset. Entity-consistent subsets and frozen inputs/hashes are saved under each benchmark output. Runtime labels never enter product CSVs or matcher features.

Windows 10 build 19045, Python 3.11.9, AMD64 Family 23 Model 104, 12 logical CPUs. Available RAM at start was not captured, so the proposed “50% available RAM” gate remains unverified. RSS uses sampled Windows working set every 0.1 seconds, not Python heap; repeated in-process runs may retain allocator memory. CPU/environment/source/policy hashes are in artifacts. Instrumentation follows [Microsoft GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo).

## Offline runs

'''+table+'''
Each group includes a separately marked warm-up plus three measurements; see [all run IDs and values](../eval/results/scale_summary.json). Core time includes normalization, retrieval, scoring, clustering; excludes output serialization and product database persistence. LLM is off. Cross-source universe is 147,000,000 pairs; 24,489 true pairs. Full 21k generated 75,952 candidates, retained 24,076 true pairs and missed 413; blocking recall 0.983135. Review rate is 78.18%; no calibration or human validation is claimed. Zero FP on this fixture is not a production error-rate guarantee.

The 5k and 10k runs first FAILED at the default 100k candidate ceiling; failure.json artifacts remain in scale_5000 and scale_10000. Reruns explicitly use experimental 250k ceiling, never silently truncate. Absolute max_postings pruning produces non-monotonic candidate counts: 21k is not proof that every smaller slice fits the default ceiling. Branch/pruning recall sensitivity remains a follow-up.

At the same full 21k universe, raising cohesion supplemental budget from 1,000 to 8,000 yielded cluster F1 0.793616 versus 0.723700, both zero FP links on generated labels. This is an exploratory compute-budget comparison, not an approved policy release. See cohesion_budget_8000. Earlier scale_21k runs used an earlier shared-contact proxy and are historical exploratory results; final policy semantics are represented by scale_21k_v3.

## Product measurements

The API benchmark imports all three CSVs into a fresh SQLite DB, checks import idempotency, submits a durable task, polls while running, reads dashboard/job/quality/review pages, and verifies a ground-truth-safe approval/apply/unmerge/idempotency cycle. It uses in-process ASGI, not a browser/network/container deployment.

Before WAL, final product run took 38.69 seconds including persistence, with 846 MiB database and about 876 MiB sampled RSS. After completion, read p95: dashboard 12.6 ms, job 3.9 ms, quality 431.6 ms, review 409.4 ms (20 samples each). One concurrent status read waited 19.68 seconds during a large write transaction; this gate failed. New databases now use WAL to permit concurrent readers; measured follow-up is product_21k_wal. Existing DBs retain their mode and need an idle, backed-up migration if WAL is desired. Never change journal mode mid-job.

Durable queue migration 005 provides request idempotency, overlap rejection, single worker, heartbeat, interrupted-task recovery and audit. API submits return 202; /tasks/{id} and /jobs/{id} expose durable state. Product DB size is a remaining optimization issue: proposals duplicate evidence. No ANN dependency was added.

## Reproduction

```powershell
venv/Scripts/python.exe -m eval.scripts.resolution_evaluate run --dataset data/synthetic/vn_seed119/dataset.json --split all --mode field-aware-address --policy config/matcher_v2_experimental.json --repeats 3 --warmup --max-seconds 600 --max-rss-mib 4096 --output eval/results/reproduced_21k
venv/Scripts/python.exe -m eval.scripts.resolution_evaluate run --dataset data/synthetic/vn_seed119/dataset.json --subset-records 5000 --split all --mode field-aware-address --policy config/matcher_v2_candidate_budget250k.json --repeats 3 --warmup --output eval/results/reproduced_5k
venv/Scripts/python.exe -m eval.scripts.resolution_product_benchmark --output eval/results/reproduced_product
```

Legacy lexical baseline remains limited to 2,000 records. Same-slice comparison is in baseline_1k; do not compare its old test split with full 21k as if denominators were identical. Deployment/Docker/browser and available-RAM gates remain unverified.
''')
write('docs/matcher_validation.md','''# Matcher validation — 2026-09-27

Implemented an opt-in evidence-v2 matcher; default conservative-v1 remains intact. Versioned policy controls weights and minimum name evidence. Shared email now mirrors phone risk; differing normalized names on a shared contact remain review candidates, including close household names. Four identical-name variants do not become shared-risk merely because there are more than three copies. Hard verified-ID/cannot-link/partial-address guards remain enforced. Missing features remain unknown and calibrated_probability remains null.

Validation-only threshold search and policy hash artifacts: eval/results/matcher_tuning/validation.json and selected_policy.json. Selected .86 is EXPERIMENTAL_NOT_APPROVED, not an approved product release. Frozen entity splits exist for synthetic data. No classifier/calibration was fitted without independent labels.

Final old-fixture regression run eval-b9f958f1c69f4021a475db6af53e0c58: blocking recall 1.0, cluster pair F1 0.884876, FP links 0, review rate 91.00%. Historical baseline F1 0.879819; prior address rules F1 0.600567, review 93.9%. The 20% relative review-reduction target was not met. Earlier less strict shared-name proxy results are exploratory, not final evidence. Full 21k results are performance/generated-label checks, not holdout quality acceptance.

Error-analysis CLI exports TP/FP/FN, true/false review, source pairs, missingness, flags, candidate branches and global blocking misses without raw contacts. No paired-independent-label quality claim is made.

```powershell
venv/Scripts/python.exe -m eval.scripts.resolution_tune --help
venv/Scripts/python.exe -m eval.scripts.resolution_error_analysis --help
```

**Next-session reminder requested by the user:** the team currently has no independently reviewed label set or assigned adjudicator. Before N1.4/N1.5/N1.8/final N1.9–1.10, ask for the dataset path and who resolves label disagreements. Follow labeling_guidelines.md and blank templates. Do not turn reviewed feedback into an unbiased recall dataset. Keep the experimental matcher optional until a new frozen holdout and risk policy are approved.
''')
write('docs/ui_acceptance.md','''# UI acceptance status — 2026-09-27

Implemented server-side review pagination (25/50), status/risk filters, approved policy/rule selectors, paginated normalization preview, evidence tables/citations, scored-edge SVG with explicit UNSCORED, post-merge source-field selection, agent tool/usage display, 409 reload and durable task polling. Poll status has its own panel so it does not overwrite Quality preview. Browser timeout reports unknown ongoing server status rather than falsely reporting FAILED.

Verification: HTTP tests include 106 proposals without duplicated IDs, Viewer 403, revision 409, async 202/idempotency and lifespan worker execution. Three Node helper behavior tests cover scored versus unscored graph, HTML escaping/unknown values and preserving disabled controls. Syntax check passed. These checks do not certify layout or browser interaction.

**Browser QA is pending:** enabled browser inventory was empty, and the native computer-use connection was unavailable. No browser screenshots or mobile/keyboard acceptance were performed. B6.1–6.5/B8.7 and N2.9–2.10 remain unchecked.

| Scenario to run in an available browser | Expected |
| --- | --- |
| Viewer, Steward, Approver logins | Correct actions visible; backend denies unauthorized writes |
| >100 review items; filters/page/detail/back | Accessible pages and current filter/page preserved |
| Import bad row, quality preview, pending rule | Errors clear; raw preserved; pending preview cannot run |
| Triad conflict and missing pair graph | Cannot-link visible; no invented positive/negative edge |
| 409 stale decision and golden edit | Reload latest revision, no stale mutation |
| Job timeout/reload/restart | Task ID persists at backend; no duplicate run; job details expose tasks |
| Resolver completed/fallback/cache | Provider use, tool trace, estimate and failure distinct |
| Golden edits, unmerge, logout, injection | Source/provenance correct, audit preserved, escaped text, session cleared |
| Desktop/mobile/keyboard | Controls usable and focus visible |

UI polling is process-local; after a full page reload use job detail task IDs to check durable backend status. Browser resume/recover convenience controls remain a follow-up. Docker/remote CI has not been run.
''')
write('docs/resolver_validation.md',f'''# Live Resolver validation — 2026-09-27

The user configured Gemini key in .env. Real run completed with process-only RESOLVER_ENABLED=1; existing .env was not rewritten. Model: {run['model_version']}. Run: {run['run_id']}; proposal: {run['proposal_id']}.

Sanitized evidence is in [live_demo.json](../eval/results/resolver_validation/live_demo.json). Completed {proof['usage'].get('llm_calls')} provider requests and {len(tools)} read-only tool calls: {', '.join(t['tool_name'] for t in tools)}. Input/output tokens 3,775/188, latency 10.234 seconds, configured estimated USD 0.00122575 (not invoice). Final recommendation REVIEW preserved ambiguous address mapping. Easy case NOT_NEEDED with zero calls. No merge tool or human approval was granted to the model.

First sandbox transport attempt failed, retained as FALLBACK with zero returned token usage; later approved network run succeeded. Transport errors now have a distinct code. Cache responses report historical usage and zero new calls; address-reference snapshot changes invalidate cache. Malformed response usage is retained for cost accounting. Offline tests cover unsafe tools, invalid evidence, schema/transport/timeout/quota failures and injection.

One synthetic successful case proves integration only. No 30–50 independent-label pilot, paired quality improvement, Steward time saving or stable p95 has been measured. N3.4–3.6/final N3.8 and B9.7 remain pending. The paired-case runner is eval/scripts/resolution_agent_evaluate.py; uncertain labels are excluded from binary quality metrics, and case-only evaluation cannot establish global recall.

```powershell
venv/Scripts/python.exe -m eval.scripts.resolution_demo --database data/live_ai_demo.db --live-ai
venv/Scripts/python.exe -m eval.scripts.resolution_agent_evaluate --help
```

Never put keys into chat or artifacts. Price variables remain estimates to be checked against the group's provider billing before a larger pilot. Next time remind the team to provide independent labels and name an adjudicator; no further live calls are required merely to repeat this proof.
''')

plan=ROOT/'docs/IMPROVEMENT_PLAN_1_4.md'
s=plan.read_text(encoding='utf-8')
s=s.replace('**kế hoạch, chưa triển khai các đầu việc mới**','**đã triển khai các phần độc lập; một số gate còn chờ nhãn/browser**')
done=['N1.1','N1.2','N1.3','N1.6','N1.7','N2.1','N2.2','N2.3','N2.4','N2.5','N2.6','N2.8','N3.2','N3.3','N3.7','N4.2','N4.4','N4.5','N4.7','N4.8','N4.9','N4.10','N4.11','N4.12']
for task in done:s=s.replace(f'- [ ] **{task}**',f'- [x] **{task}**')
s=s.replace('Các options này **chưa có trong code hiện tại**.','Các options này đã được bổ sung; xem report cập nhật.')
s+='''
## 7. Cập nhật thực hiện — 2026-09-27

Checkbox đánh dấu phần triển khai có bằng chứng; gate N1/N2/N3/N4 tổng vẫn chưa nghiệm thu toàn bộ. Xem matcher_validation.md, ui_acceptance.md, resolver_validation.md, performance_21k.md và checklist chính.

- N1.4–1.5/1.8: **chờ nhóm**. Người dùng xác nhận chưa có bộ nhãn được kiểm tra độc lập, sẽ bổ sung sau. Agent phải nhắc lại đường dẫn dữ liệu và người chốt bất đồng ở lần tiếp theo. Templates và quy trình đã có; không tự sinh nhãn để thay cho review độc lập.
- N1.9–1.10: đã tuning validation synthetic và chạy regression; chưa calibration, holdout mới hoặc release policy. Review reduction 20% chưa đạt.
- N2.7: task queue/polling đã code/test; tiện ích resume/recover trên UI và browser timeout QA chưa hoàn tất. N2.9–2.10 vẫn chờ browser thật.
- N3.1: key đã do người dùng cấu hình; real run thành công bằng override process. Giá là estimate, chưa đối chiếu billing; không sửa .env. N3.4–3.6/3.8 còn pilot/paired quality/rubric chưa có labels. B8.9 có thể tick theo trace thật.
- N4.1/4.3: dataset/hash/source/CPU/RSS/core-stage/product DB được ghi; chưa available RAM, chi tiết index/query/export riêng. N4.6 branch/pruning ablation chưa đủ. Đã phát hiện candidate ceiling và read lock; không che run lỗi.
- N4.4 subsets whole-entity, không bảo đảm chính xác mỗi nguồn bằng nhau ở từng slice nhỏ; full 21k cân bằng 7k/nguồn. Repeats/warmup và manifests giữ riêng.
- N4.11 đối chiếu baseline cùng slice 1k, không áp baseline quadratic lên 21k. N4.12 report công khai gate chưa đạt/kiểm chứng.
- Docker và CI remote chưa chạy; P2 vẫn hoãn. Không thay default policy bằng matcher thử nghiệm.
'''
plan.write_text(s,encoding='utf-8')

p=ROOT/'IMPLEMENTATION_CHECKLIST.md';s=p.read_text(encoding='utf-8')
s=s.replace('- [ ] **B8.9**','- [x] **B8.9**')
s=s.replace('B8.7 chờ browser, B8.9 chờ AI thật','B8.7 chờ browser; B8.9 đã có Gemini real trace')
s=s.replace('mỗi nguồn 7.000; chưa benchmark full scale','mỗi nguồn 7.000; đã benchmark full 21k, xem docs/performance_21k.md')
s=s.replace('| AI call thật | Chờ cấu hình | Chưa có key được cấu hình; chạy `resolution_demo --live-ai` rồi kiểm trace trước khi tick B8.9 |','| AI call thật | Đã xác minh | Gemini real run agent-4559b66792e149d994b9c83d821b7ce3; 3 requests/2 tools; xem docs/resolver_validation.md |')
s=s.replace('Queue hiện hiển thị tối đa 100 mục; API có pagination, UI chưa có controls phân trang.','UI đã có pagination 25/50 và filters; HTTP thử 106 proposals. Browser QA vẫn chưa thực hiện.')
s=s.replace('**B8.9 vẫn chưa tick** cho đến khi Gemini thật hoàn tất và có run/tool trace/usage.','**B8.9 đã tick:** Gemini thật hoàn tất; trace/usage trong eval/results/resolver_validation/live_demo.json.')
s+='''
## 10. Tiến độ N1–N4 — 2026-09-27

**Nhắc lại lần sau theo yêu cầu người dùng:** nhóm chưa có bộ nhãn được người kiểm tra độc lập hoặc người phụ trách chốt bất đồng. Người dùng sẽ cập nhật sau. Khi tiếp tục N1/AI quality, hỏi đường dẫn bộ nhãn và người adjudication; không yêu cầu key qua chat. Gemini key đã được người dùng cấu hình trong .env và đã kiểm tra real call.

| Bước | Đã làm và bằng chứng | Chưa hoàn tất |
| --- | --- | --- |
| N1 | Error-analysis/label templates; policy v2 opt-in; shared email/phone guards; validation-only tuning; regression F1 0.884876, FP 0 | Nhãn độc lập, holdout mới, classifier/calibration/release; mục tiêu giảm review 20% chưa đạt |
| N2 | Pagination/filter, versions, evidence/graph, golden edit, trace, async task polling; API >100 proposals; 3 Node helper tests | Browser QA desktop/mobile/keyboard; UI resume/recovery tiện dụng |
| N3 | Gemini 3 requests, 2 read tools, REVIEW; easy 0 calls; usage/cache/failure tests và sanitized trace | Pilot 30–50 ca có nhãn, paired quality và đánh giá Steward; price billing |
| N4 | Warmup + 3 repeats 1k/5k/10k/21k; full offline và API import/persist/merge/unmerge; RSS; durable queue/heartbeat; WAL cho DB mới | Pruning branch ablation, available-RAM gate, persistence size optimization; đọc concurrent đo riêng trong report |

Files chính: field_matching.py/candidates.py/resolution_workflow.py; migration 005 và resolution_tasks.py; API resolution.py; workspace.js/css; resolver.py; evaluation/resource/tuning/error-analysis/agent/product CLI. Báo cáo: [matcher](docs/matcher_validation.md), [UI](docs/ui_acceptance.md), [AI](docs/resolver_validation.md), [scale](docs/performance_21k.md). Chi tiết checkbox ở [kế hoạch](docs/IMPROVEMENT_PLAN_1_4.md).

Tests local cuối: 92 Python tests; Ruff pass; JS syntax pass; 3 Node helper tests (không thay browser). Queue/restart/stale/raw/rollback/failure tests có behavior assertions. CI đã thêm lint/evaluation và Node tests nhưng chưa push/chạy remote. Các thư mục results giữ cả run lỗi và thử nghiệm; không dùng run proxy shared-name cũ làm kết luận cuối.

Giới hạn: matcher v2 chưa được release; default conservative-v1 vẫn nguyên. Raw không bị sửa. AI không có quyền merge. SQLite DB prototype gần 0.85 GiB ở 21k còn cần tối ưu evidence/proposal duplication. Không có Docker/browser khả dụng; không tự đánh dấu các gate đó hoàn tất.
'''
p.write_text(s,encoding='utf-8')

p=ROOT/'IMPLEMENTATION_NOTES.md';s=p.read_text(encoding='utf-8')
s=s.replace('100 review items and has no pagination controls yet. Custom rule/policy selection for\n  running jobs and post-merge field edits currently use API; UI run uses built-in versions.','100 items in the initial implementation; now has pagination/filter controls, approved\n  rule/policy selectors and post-merge field selection. Browser acceptance is still pending.')
s=s.replace('- B8.9: real AI not run because no Gemini key configured. Scripted-provider integration\n  tests are offline evidence only. Follow `docs/masterone_workspace.md` for `--live-ai`.','- B8.9: now verified with live Gemini, 3 requests / 2 read-only tools; see\n  `docs/resolver_validation.md`. Independent-label AI quality ablation remains pending.')
s=s.replace('Full matching/performance benchmark on 21k not executed.','Full 21k offline and API product performance now measured; see docs/performance_21k.md.')
s+='''
## N1–N4 implementation update (2026-09-27)

Evidence-v2 is opt-in experimental; default matcher remains conservative-v1. Validation tuning uses generated labels only; no new independent holdout, calibration or classifier is claimed. User explicitly reports no independent label set/adjudicator yet: reminder recorded in checklist and labeling guide.

Migration 005 adds durable async tasks, request hashes, heartbeat, overlap prevention, worker audit and interrupted-run recovery. Existing synchronous route remains compatible. New DBs use SQLite WAL for concurrent reads; existing databases keep their mode and require an idle migration. Indexing cluster edges and avoiding migration/seed writes on every read reduce overhead. UI polling is not a durable browser subscription; task state itself is durable.

Real Gemini proof and 21k artifacts are now available. Old summary and initial exploratory proxy runs remain historical; use final v3 regression and scale reports for current claims. Full synthetic scale has no independent quality guarantee. Candidate budget failures at 5k/10k were exported and rerun explicitly with 250k experimental ceiling, not truncated silently. Resource accounting excludes export in core time and distinguishes product persistence. Available RAM/branch sensitivity/browser/Docker/remote CI are pending.

Verification updated to 92 Python tests and 3 Node behavior tests, Ruff and syntax pass. Documents: matcher_validation.md, ui_acceptance.md, resolver_validation.md, performance_21k.md. Large prototype SQLite evidence/proposal duplication remains a size concern; downstream rollback and organization matching remain outside accepted scope.
'''
p.write_text(s,encoding='utf-8')

p=ROOT/'docs/masterone_workspace.md';s=p.read_text(encoding='utf-8')
s=s.replace('records trong split. Chưa chạy matching/benchmark đầy đủ trên 21.000 rows.','records trong split. Đã chạy full 21k; xem performance_21k.md và các run IDs.')
s=s.replace('Memory là Python tracemalloc peak, không phải full-process RSS.','Run cũ dùng Python tracemalloc; CLI mới đo process RSS và tách warmup/repeats.')
s=s.replace('LLM quality ablation chưa chạy khi không có key/labels độc lập; không suy ra recall từ reviewed feedback.','AI thật đã chạy; LLM quality ablation còn chờ labels độc lập, không suy ra recall từ reviewed feedback.')
s+='''
## Các cải tiến N1–N4

POST /api/v2/jobs/{id}/submit trả 202/task_id, worker tự chạy trong lifespan. GET /api/v2/tasks/{id} và job detail cung cấp state/error/result; không retry run chỉ vì browser timeout. Queue single-worker/heartbeat durable, migration 005 tự áp dụng. DB mới dùng WAL; DB cũ không tự đổi mode giữa lúc chạy.

```powershell
node --test tests/ui/workspace.test.cjs
venv/Scripts/python.exe -m eval.scripts.resolution_evaluate run --dataset data/synthetic/vn_seed119/dataset.json --split all --mode field-aware-address --policy config/matcher_v2_experimental.json --repeats 3 --warmup --max-seconds 600 --max-rss-mib 4096 --output eval/results/reproduced_21k
venv/Scripts/python.exe -m eval.scripts.resolution_product_benchmark --output eval/results/reproduced_product
```

Evidence-v2 configs là thử nghiệm chưa approved; import policy vẫn cần Approver/normal approval. Không đổi default matcher. Review UI có filters/pages/approved versions và post-merge field selection. Xem matcher_validation.md/ui_acceptance.md/resolver_validation.md/performance_21k.md để phân biệt code đã kiểm tra với gate chưa nghiệm thu. Nhóm sẽ bổ sung nhãn độc lập sau; nhắc lại khi tiếp tục quality validation.
'''
p.write_text(s,encoding='utf-8')
print(json.dumps({'real_tools':[t['tool_name'] for t in tools],'groups':[(r['group'],r['measured_runs']) for r in rows]}))
