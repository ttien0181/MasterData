import json
import math
from pathlib import Path

root = Path(__file__).resolve().parents[1]
report = json.loads((root/'eval/results/product_21k_wal_isolated/product_metrics.json').read_text(encoding='utf-8'))
samples = sorted(report['concurrent_task_reads_seconds'])
p95 = samples[math.ceil(.95*len(samples))-1]
summary = {
    'task_id':report['task_id'],
    'pipeline_seconds':report['times']['pipeline_including_persistence'],
    'concurrent_read_samples':len(samples),
    'concurrent_read_p95_seconds':p95,
    'concurrent_read_max_seconds':max(samples),
    'rss_peak_mib':report['resources']['process_rss_peak_sampled_bytes']/1048576,
    'db_mib':report['db_bytes']/1048576,
    'after_completion_p95_seconds':{path:values['p95'] for path,values in report['read_latency_seconds'].items()},
    'scope':'One isolated in-process ASGI product run; warmup/repeats statistics apply to offline runs only.'
}
(root/'eval/results/product_21k_wal_isolated/summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
p=root/'docs/performance_21k.md'
s=p.read_text(encoding='utf-8')
s += f'''
## Final WAL follow-up

Isolated task `{report['task_id']}`: import 21k, pipeline including persistence **{summary['pipeline_seconds']:.2f} seconds**, RSS **{summary['rss_peak_mib']:.0f} MiB**, DB **{summary['db_mib']:.0f} MiB**. {len(samples)} concurrent status reads: p95 **{p95:.3f}s**, maximum **{max(samples):.3f}s**. Per-endpoint post-completion values are in [summary.json](../eval/results/product_21k_wal_isolated/summary.json). Import/apply/unmerge/idempotency assertions passed. This is one product measurement, not a stable multi-run product percentile. The preceding WAL run (76.19s) overlapped another benchmark and is retained as a contention observation, not the isolated result.

On the frozen 1k slice, baseline / baseline-safe / evidence-v2 / evidence-v2+address cluster F1: 0.833333 / 0.833333 / 0.907707 / 0.866173; all blocking recall 1.0 and zero FP generated-label links. Artifacts: baseline_1k/ablation_summary.json. This comparison does not establish real-data generalization.

Product and offline cluster counts differ slightly even with the same field scores: product IDs are source-scoped hashes and the greedy guard tie order differs from evaluation IDs. Product has 11,707 clusters / 2,731 suspicious proposals versus offline 11,667 / 2,684, with identical candidate and score bucket counts. Do not reuse offline cluster quality numbers as product-applied merge rates. Tie-order sensitivity and normalized evidence storage remain follow-ups.

Final checks: 93 Python tests, 3 Node behavior tests, Ruff, JS syntax and git diff --check passed locally. WAL startup race initially failed the concurrent-migration regression; bounded busy retry fixed it, and the full suite passed. Browser/Docker/CI-remote and available-RAM gates remain pending.
'''
p.write_text(s,encoding='utf-8')
for name in ('IMPLEMENTATION_CHECKLIST.md','IMPLEMENTATION_NOTES.md'):
    p=root/name;s=p.read_text(encoding='utf-8')
    s=s.replace('92 Python tests','93 Python tests')
    s += f'\nFinal isolated WAL product: {summary["pipeline_seconds"]:.2f}s; concurrent read p95 {p95:.3f}s/max {max(samples):.3f}s; task {report["task_id"]}. See eval/results/product_21k_wal_isolated/summary.json. Full suite 93 passed after the WAL startup-race fix.\n'
    p.write_text(s,encoding='utf-8')
p=root/'docs/IMPROVEMENT_PLAN_1_4.md';s=p.read_text(encoding='utf-8')
s=s.replace('- [x] **N2.2**','- [ ] **N2.2**')
s+='\n- N2.2 code/pagination/static >100 fixture đã kiểm tra; chưa browser và snapshot stability khi proposals thay đổi giữa hai trang. Giữ checkbox chưa hoàn tất để không bỏ quên nghiệm thu này.\n'
p.write_text(s,encoding='utf-8')
print(json.dumps(summary,indent=2))
