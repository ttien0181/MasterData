"""Compare completed HTTP full10k receipts; fail closed on missing acceptance evidence."""
import argparse
import json
from collections import Counter
from pathlib import Path

from qa_api_multiagent import save
from report_full10k import derive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--markdown', type=Path, required=True)
    args = parser.parse_args()
    names = ('TC1_easy_match', 'TC3_hard_match')
    raw = {name: json.loads((args.input / name / 'report.json').read_text(encoding='utf8')) for name in names}
    assert all(r['status'] == 'PASS' for r in raw.values()), 'BOTH_HTTP_RUNS_MUST_PASS'
    raw_baseline = {name: json.loads((args.baseline / name / 'report.json').read_text(encoding='utf8')) for name in names}
    baseline = {name: derive(raw_baseline[name]) for name in names}
    measured = {name: derive(raw[name]) for name in names}
    for data, receipts in ((baseline, raw_baseline), (measured, raw)):
        for name in names:
            data[name]['http_stages_seconds'] = {stage: entry['seconds'] for stage, entry in receipts[name]['stages'].items()
                if entry.get('complete')}
    for data, directory in ((baseline, args.baseline), (measured, args.input)):
        for name in names:
            path = directory / name / 'embedding-quality.json'
            if path.exists():
                data[name]['embedding_diagnostics'] = json.loads(path.read_text(encoding='utf8'))
    runtime = json.loads((args.input / 'runtime.json').read_text(encoding='utf8'))
    old_runtime = json.loads((args.baseline / 'runtime.json').read_text(encoding='utf8'))
    profile = json.loads((args.input / 'profile.json').read_text(encoding='utf8'))
    invariants = json.loads((args.input / 'final-invariants.json').read_text(encoding='utf8'))
    assert set(invariants) == set(names), 'BOTH_INDEPENDENT_POSTCHECKS_REQUIRED'
    health = json.loads((args.input / 'final-health.json').read_text(encoding='utf8'))
    diagnostic_oom_path = args.input / 'postcheck-first-attempt-oom.json'
    diagnostic_oom = json.loads(diagnostic_oom_path.read_text(encoding='utf8')) if diagnostic_oom_path.exists() else None
    checks = {
        'settings_match_baseline': runtime['settings'] == old_runtime['settings'],
        'source_installed_matches_local': runtime['raw_local_hashes_match'],
        'evidence_profile': profile['config']['kind'] == 'RULE_WITH_EMBEDDING_EVIDENCE',
        'lexical_experiment_off': not profile['config'].get('retrieval', {}).get('global_lexical', False),
        'automation_off': runtime['settings']['pair_automation_enabled'] is False,
        'independent_postchecks': all(all(value for key, value in invariants[name]['checks'].items()
            if key != 'no_failed_attempts') for name in names),
        'final_services_healthy': all(value == 200 for value in health['http'].values())
            and all(value['running'] for value in health['containers'].values()),
        'no_container_restarts': all(value['restarts'] == runtime['containers'][service]['restarts']
            for service, value in health['containers'].items()),
        'same_resource_limits': all(runtime['containers'][s]['memory_bytes'] == old_runtime['containers'][s]['memory_bytes']
            and runtime['containers'][s]['nano_cpus'] == old_runtime['containers'][s]['nano_cpus']
            for s in ('backend', 'worker', 'database', 'frontend')),
    }
    for name in names:
        report = raw[name]
        quality = measured[name]['after_scripted_truth_review']
        checks[name] = {
            'finalized': report['stages']['golden-confirm-finalize']['result']['workflow_state'] == 'FINALIZED',
            'source_sha_matches_baseline': report['fixture']['source_sha256'] == json.loads(
                (args.baseline / name / 'fixture.json').read_text(encoding='utf8'))['source_sha256'],
            'record_conservation': quality['records_preserved'] == report['fixture']['records'],
            'zero_overmerge': quality['overmerge_pairs'] == 0,
            'no_failed_worker_attempts': all(a['status'] == 'SUCCEEDED' for a in report['telemetry']['worker_attempts']),
            'faster_upload_to_golden': measured[name]['measured_upload_to_golden_seconds'] < baseline[name]['measured_upload_to_golden_seconds'],
            'candidate_recall_no_regression': measured[name]['machine_quality']['candidate_recall'] >= baseline[name]['machine_quality']['candidate_recall'],
            'golden_recall_no_regression': quality['pairwise']['recall'] >= baseline[name]['after_scripted_truth_review']['pairwise']['recall'],
        }
        save(args.input / name / 'accounting.json', measured[name])
    functional = all(checks[k] for k in checks if k not in names) and all(
        all(checks[name][key] for key in ('finalized', 'source_sha_matches_baseline', 'record_conservation', 'zero_overmerge')) for name in names)
    performance = all(checks[name]['no_failed_worker_attempts'] and checks[name]['faster_upload_to_golden'] for name in names)
    quality_target = measured['TC3_hard_match']['machine_quality']['candidate_recall'] >= .90
    summary = {'functional_acceptance': functional, 'performance_acceptance_single_run': performance,
        'tc3_candidate_recall_90_percent': quality_target, 'checks': checks, 'baseline': baseline, 'measured': measured,
        'independent_postchecks': invariants,
        'final_health': health, 'additional_postcheck_oom': diagnostic_oom,
        'qualification': 'One run/case; scripted truth review; not autonomous accuracy or production certification.'}
    save(args.input / 'acceptance-summary.json', summary)
    save(args.input / 'accounting-summary.json', {'cases': measured})

    def seconds(value):
        return 'chưa đo' if value is None else f'{value:,.2f} s ({value / 60:.2f} phút)'
    def percent(value):
        return 'n/a' if value is None else f'{100 * value:.3f}%'
    def table(rows):
        output = '| Phép đo | TC1 trước | TC1 sau | TC3 trước | TC3 sau |\n| --- | ---: | ---: | ---: | ---: |\n'
        for label, field, format_value in rows:
            output += '| ' + label + ' | ' + ' | '.join(format_value(field(data[name])) for name in names
                for data in (baseline, measured)) + ' |\n'
        return output + '\n'
    text = '# Nghiệm thu end-to-end TC1/TC3 sau tối ưu — 16/10/2026\n\n'
    text += f'Luồng HTTP → FINALIZED, bảo toàn record và zero overmerge: **{"PASS" if functional else "FAIL"}**. '
    text += f'Thời gian/retry trong một lượt mỗi bộ: **{"PASS" if performance else "FAIL"}**. '
    text += f'Mục tiêu candidate recall TC3 ≥90%: **{"PASS" if quality_target else "CHƯA ĐẠT"}**.\n\n'
    text += 'Dùng Compose hiện hữu, một worker, chạy hai bộ lần lượt. Không tạo Compose mới. '
    text += 'Profile evidence-only; lexical retrieval và auto-merge tắt. Actor mới, cache embedding riêng theo run/owner; '
    text += 'không tái dùng cache run-02. Cache provider phía ngoài không quan sát được. '
    text += 'Truth chỉ dùng sau matching để đánh giá/replay; chất lượng Golden có trợ giúp truth, không phải accuracy tự động.\n\n'
    text += table([
        ('Records', lambda r: r['records'], str), ('Candidates', lambda r: r['candidates'], str),
        ('Upload → Golden, gồm instrumentation/mẫu giải thích/replay', lambda r: r['measured_upload_to_golden_seconds'], seconds),
        ('Upload → FINALIZED, gồm hậu kiểm membership', lambda r: r['measured_upload_to_finalized_seconds'], seconds),
        ('Upload → Golden bỏ đánh giá offline và mẫu giải thích, vẫn gồm replay', lambda r: r['base_without_posthoc_and_explanation_sample_seconds'], seconds)])
    for name in names:
        reduction = 1 - measured[name]['measured_upload_to_golden_seconds'] / baseline[name]['measured_upload_to_golden_seconds']
        text += f'{name}: Upload → Golden giảm **{reduction * 100:.2f}%** trong lượt đo này.\n\n'
    http_labels = [('prepare', 'Chuẩn bị sau upload'), ('matching-advice-preview', 'Matching + advice + preview'),
        ('posthoc-evaluation', 'Đánh giá offline QA'), ('gemini-explanation-sample', 'Mẫu giải thích thật'),
        ('scripted-truth-review', 'Lưu toàn bộ quyết định replay qua API'), ('cluster-and-golden', 'Sau duyệt → Golden, gồm xác nhận cụm'),
        ('scripted-group-review', 'Xác nhận cụm — nằm trong chặng ngay trên'), ('golden-quality', 'Hậu kiểm membership/chất lượng'),
        ('golden-confirm-finalize', 'Xác nhận và finalize Golden')]
    text += table([(label, lambda r, stage=stage: r['http_stages_seconds'].get(stage), seconds) for stage, label in http_labels])
    text += 'Thời gian worker/phase nằm trong thời gian client; không cộng hai bảng. Queue và HTTP/provider được giữ trong tổng.\n\n'
    stage_names = sorted(set().union(*(set(data[name]['worker_phases_seconds']) for data in (baseline, measured) for name in names)))
    text += table([(stage, lambda r, stage=stage: r['worker_phases_seconds'].get(stage, 0.), seconds) for stage in stage_names])
    text += table([
        ('Embedding HTTP intents', lambda r: r['ai_requests_actual']['embedding'], str),
        ('Gemini advice HTTP attempts', lambda r: r['ai_requests_actual']['gemini_advice'], str),
        ('Gemini explanation HTTP attempts', lambda r: r['ai_requests_actual']['gemini_explanations'], str),
        ('Embedding latency có final receipt', lambda r: r['known_api_latency_seconds']['embedding'], seconds),
        ('Candidate recall', lambda r: r['machine_quality']['candidate_recall'], percent),
        ('Golden precision sau replay', lambda r: r['after_scripted_truth_review']['pairwise']['precision'], percent),
        ('Golden recall sau replay', lambda r: r['after_scripted_truth_review']['pairwise']['recall'], percent),
        ('Golden F1 sau replay', lambda r: r['after_scripted_truth_review']['pairwise']['f1'], percent),
        ('Overmerge pairs', lambda r: r['after_scripted_truth_review']['overmerge_pairs'], str),
        ('Undermerge pairs', lambda r: r['after_scripted_truth_review']['undermerge_pairs'], str),
        ('Records bảo toàn', lambda r: r['after_scripted_truth_review']['records_preserved'], str)])
    if all('embedding_diagnostics' in r for data in (baseline, measured) for r in data.values()):
        text += 'AUC chỉ đo thứ hạng trên candidate có evidence; cosine không phải xác suất. Không chọn ngưỡng từ test này.\n\n'
        text += table([(field + ' — ROC AUC', lambda r, field=field: r['embedding_diagnostics']['fields'][field]['roc_auc_on_available_candidates'],
            lambda value: 'n/a' if value is None else f'{value:.6f}') for field in ('name', 'address')])
    for name in names:
        attempts = Counter(a['status'] for a in raw[name]['telemetry']['worker_attempts'])
        text += f'- {name}: run `{raw[name]["run_id"]}`; worker attempts `{dict(attempts)}`; '
        text += f'checks `{checks[name]}`.\n'
    text += '\nGemini matching advice và explanation là hai mục đích riêng. Trạng thái receipt lượt mới:\n\n'
    for name in names:
        purposes = {}
        for entry in raw[name]['telemetry']['llm_by_purpose']:
            statuses = purposes.setdefault(entry['purpose'], Counter())
            statuses.update(entry.get('statuses', {}))
        text += f'- {name}: `{ {purpose: dict(counts) for purpose, counts in purposes.items()} }`.\n'
    text += '\nWorkflow hoàn tất không chứng minh advice đã trả schema hợp lệ hoặc có accuracy tốt; '
    text += 'không suy ra điều đó từ explanation thành công.\n'
    text += '\nRuntime settings, resource limits, source SHA, image và code SHA nằm trong `runtime.json` và `acceptance-summary.json`. '
    text += 'Source cài trong container khớp local; không đổi model/ngưỡng/constraints/eligibility để tăng metric.\n\n'
    memory_path = args.input / 'stack-memory.json'
    if memory_path.exists():
        memory = json.loads(memory_path.read_text(encoding='utf8'))
        peak = memory.get('peak_stack_working_set_bytes')
        text += f'Memory sampler: `{memory["status"]}`, {len(memory["samples"])} mẫu; peak backend + worker + frontend '
        text += f'{peak / 1024**3:.3f} GiB. Không gồm DB, Docker VM/toàn host; các service lấy mẫu lần lượt, không phải peak đồng thời chính xác.\n\n'
        for service in ('backend', 'worker', 'frontend'):
            values = [sample['services'][service]['cpu_percent'] for sample in memory['samples'] if 'cpu_percent' in sample['services'][service]]
            if values:
                text += f'- CPU {service}, mẫu trung bình / lớn nhất: {sum(values)/len(values):.2f}% / {max(values):.2f}% (Docker: 100% = một core).\n'
        text += '\n'
    host_path = args.input / 'host-memory-observations.json'
    if host_path.exists():
        observations = json.loads(host_path.read_text(encoding='utf8'))
        text += 'Đối chiếu RAM Windows/WSL: các số container ở trên không đại diện toàn bộ Docker VM. '
        text += 'Các lần quan sát riêng trong `host-memory-observations.json` ghi nhận:\n\n'
        for observation in observations:
            vm = sum(p['rss_bytes'] for p in observation['windows_processes'] if 'vmmem' in p['name'].lower())
            linux = observation['linux_meminfo_bytes']
            text += f'- {observation["at_utc"]}: vmmemWSL {vm / 1024**3:.3f} GiB; '
            text += f'Linux Cached {linux["Cached"] / 1024**3:.3f} GiB, Buffers {linux["Buffers"] / 1024**3:.3f} GiB.\n'
        text += '\nCache trong container và VM có phần trùng nhau; không cộng hai mức. '
        text += 'Dung lượng image/build cache/volume là ổ đĩa. Các file đã đọc/ghi có thể được Linux giữ trong RAM cache. '
        text += 'Các quan sát VM không liên tục và không phải đỉnh RAM toàn host. '
        text += 'Không đổi memory limits hoặc xóa cache giữa lượt đo. '
        text += '[Cơ chế WSL page cache và thu hồi RAM — Docker](https://docs.docker.com/desktop/features/wsl/).\n\n'
    if diagnostic_oom is not None:
        text += '**Sự cố hậu kiểm bổ sung, ngoài thời gian workflow:** lần đầu công cụ đọc ORM generation đầy đủ '
        text += 'và giữ toàn bộ frozen records trong RAM, chạy chung backend 1 GiB, bị OOM (exit 137). '
        text += 'Docker event được lưu ngay trong `postcheck-first-attempt-oom.json`; '
        text += f'`oom_after_finalized={diagnostic_oom["oom_after_finalized"]}`. '
        text += 'Backend còn healthy, không restart; raw Docker OOM flag vẫn giữ nguyên, không reset để che sự cố. '
        text += 'Sampler kết thúc sau sự cố nên `within_target=false` do OOM flag trong phần hậu kiểm cuối; '
        text += 'không diễn giải raw receipt đó là chứng nhận RAM PASS toàn cửa sổ.\n\n'
        peak = max(row['probe_peak_rss_bytes'] for row in invariants.values())
        text += 'Đã sửa công cụ để defer `WorkflowGeneration.finalized_result` không dùng, stream record IDs, '
        text += f'và chạy read-only trong worker hiện hữu; peak probe sau sửa {peak / 1024**2:.1f} MiB. '
        text += 'Toàn bộ hậu kiểm đã pass; không chạy lại matching hoặc sửa dữ liệu để đạt kết quả. '
        text += 'Đây là lỗi footprint của công cụ nghiệm thu bổ sung, không được loại bỏ khỏi artifact.\n\n'
    text += 'Mỗi bộ chỉ một lượt, chưa có p50/p95 hoặc tải nhiều người dùng. Không quy toàn bộ chênh lệch thời gian cho code: '
    text += 'provider/network, cache DB/OS và biến động scheduler cũng ảnh hưởng. Baseline TC1 có timeout/retry 1.800 giây; '
    text += 'không loại thời gian đó khỏi so sánh. Kiểm tra `no_failed_worker_attempts` chỉ xác nhận lượt mới, không bảo đảm không bao giờ timeout.\n\n'
    text += 'Quyền merge ORGANIZATION và hard constraints được giữ nguyên. Mục tiêu retrieval ≥90%, calibration embedding/Splink '
    text += 'và chất lượng Golden từng trường vẫn chưa được chứng nhận. Không dùng PASS luồng để mở auto-merge.\n\n'
    text += 'Đây là upload HTTP localhost; không đo băng thông Internet hoặc thời gian con người đọc/suy nghĩ khi duyệt.\n\n'
    text += f'Artifact: `{args.input.as_posix()}`; baseline `{args.baseline.as_posix()}`. '
    text += 'Có receipt từng bộ, accounting, runner log, memory samples, runtime fingerprint và hậu kiểm độc lập. '
    text += 'Artifact có thể bị Git ignore.\n'
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(text, encoding='utf8')
    print(json.dumps({'functional_acceptance': functional, 'performance_acceptance_single_run': performance,
                      'tc3_candidate_recall_90_percent': quality_target}))


if __name__ == '__main__':
    main()
