"""Offline report from numeric QA receipts; never opens credentials or calls AI."""
import argparse
import json
from pathlib import Path


def summarize_run(run):
    telemetry = run.get('telemetry', {})
    llm = telemetry.get('llm_by_purpose', [])
    embedding = telemetry.get('embedding_by_position', {})
    attempts = [attempt for op in telemetry.get('operations', [])
                for attempt in (op.get('telemetry') or {}).get('attempts', [])]
    stages = {}
    for attempt in attempts:
        for name, seconds in attempt.get('stages', {}).items():
            stages[name] = stages.get(name, 0.) + seconds
    quality = run.get('machine_quality_before_truth_replay', {})
    return {'case': Path(run['fixture']['dataset']).parts[-3], 'variant': run['variant'],
            'run_id': run['run_id'], 'workflow_status': run['status'],
            'records': run['fixture']['record_count'],
            'configured_ai_execution_mode': telemetry.get('configured_ai_execution_mode', telemetry.get('ai_execution_mode', 'UNRECORDED_BASELINE')),
            'candidate_count': quality.get('candidate_count'),
            'machine_candidate_recall': quality.get('candidate_recall'),
            'machine_quality': quality,
            'final_after_truth_review': run.get('final_quality_after_scripted_truth_review'),
            'ai_http_attempts': sum(row['http_attempts'] for row in embedding.values()) + sum(row['http_attempts'] for row in llm),
            'embedding_http_attempts': sum(row['http_attempts'] for row in embedding.values()),
            'llm_http_attempts': sum(row['http_attempts'] for row in llm),
            'llm_successes': sum(row['successes'] for row in llm),
            'llm_failures': sum(row['http_attempts'] - row['successes'] for row in llm),
            'embedding_by_model_field': telemetry.get('embedding_by_model_field', []),
            'embedding_by_position': embedding, 'llm_by_purpose': llm,
            'client_total_seconds': run.get('client_total_seconds'),
            'client_stages': run.get('http_stages', []),
            'worker_seconds': sum(row['total_seconds'] for row in attempts) if attempts else None,
            'worker_stages': stages,
            'local_model_evaluations': telemetry.get('local_model_evaluations', []),
            'worker_attempts': telemetry.get('worker_attempts', [])}


def markdown(rows):
    lines = ['# Nghiệm thu API-only trên dữ liệu nhỏ — 16/10/2026', '',
             'PASS workflow không có nghĩa tất cả lời gọi AI thành công. Golden sau truth replay là QA mô phỏng người duyệt, không phải accuracy tự động.', '',
             '| Bộ | Mode | Records | Candidates | Recall ứng viên | Embedding calls | LLM calls | LLM thành công/lỗi | Tổng client (s) |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        recall = row['machine_candidate_recall']
        elapsed = row['client_total_seconds']
        lines.append(f"| {row['case']} | {row['variant']} | {row['records']} | {row['candidate_count']} | "
                     f"{recall if recall is not None else 'unknown'} | {row['embedding_http_attempts']} | "
                     f"{row['llm_http_attempts']} | {row['llm_successes']}/{row['llm_failures']} | "
                     f"{round(elapsed, 3) if elapsed is not None else 'unrecorded'} |")
    for row in rows:
        lines += ['', f"## {row['case']} / {row['variant']}", '', f"Run: `{row['run_id']}`; cấu hình server lúc đọc telemetry: `{row['configured_ai_execution_mode']}` (không suy ngược chế độ lịch sử).", '',
                  '| Giai đoạn client | Giây |', '| --- | ---: |']
        lines += [f"| {stage['stage']} | {stage['seconds']:.3f} |" for stage in row['client_stages']]
        lines += ['', '| Giai đoạn worker, gồm mọi attempt | Giây |', '| --- | ---: |']
        lines += [f'| {name} | {seconds:.3f} |' for name, seconds in sorted(row['worker_stages'].items())]
        lines += ['', '| Provider / model / mục đích | HTTP attempts | HTTP OK (embedding) / output hợp lệ (LLM) | Latency (s) | Trạng thái |',
                  '| --- | ---: | ---: | ---: | --- |']
        for item in row['embedding_by_model_field']:
            lines.append(f"| {item['provider']} / {item['model']} / {item['position']}:{item['field']} | "
                         f"{item['http_attempts']} | {item['responses']-item['failed_responses']} | "
                         f"{item['latency_seconds']:.3f} | unresolved={item['unresolved_intents']} |")
        for item in row['llm_by_purpose']:
            lines.append(f"| {item['provider']} / {item['model']} / {item['purpose']} | {item['http_attempts']} | "
                         f"{item['successes']} | {item['latency_seconds']:.3f} | {item['statuses']} |")
    lines += ['', 'Worker time nằm trong client time, không cộng hai bảng. Latency là tổng từng request, không phải wall time toàn pipeline.',
              'Embedding HTTP intents có thể chưa tới provider nếu tiến trình bị ngắt. Unknown token/cost vẫn là unknown trong JSON.',
              'Rule, Coordinator, retrieval/cosine, clustering và Golden selection: 0 model API call. Splink phải không EVALUATED khi API-only.',
              'Subset tổng hợp nhỏ; không suy rộng recall này thành TC3/10k, không dùng tập test để hiệu chỉnh ngưỡng.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='QA output directory with summary.json')
    parser.add_argument('--output', type=Path, required=True, help='Markdown destination; JSON sibling also written')
    args = parser.parse_args()
    summary = json.loads((args.input / 'summary.json').read_text(encoding='utf8'))
    rows = [summarize_run(run) for run in summary['runs']]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(rows), encoding='utf8')
    args.output.with_suffix('.json').write_text(json.dumps({'status': summary['status'], 'runs': rows}, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'status': summary['status'], 'runs': len(rows), 'ai_http_attempts': sum(row['ai_http_attempts'] for row in rows)}))


if __name__ == '__main__':
    main()
