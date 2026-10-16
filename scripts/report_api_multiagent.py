"""Read QA receipts and owned telemetry; generate numeric delivery artifacts.

Uses the ignored QA access files without printing credentials. No mutations or
provider API requests. Source rows and prompts are excluded from the report.
"""
import json
from collections import Counter
from pathlib import Path

import httpx
from qa_api_multiagent import ROOT, metrics, save

BASE = ROOT / 'artifacts/api-multiagent-20261015'


def main():
    groups, quality = {}, []
    for folder in ('live-01', 'live-evidence-01', 'live-assistance-01'):
        summary = json.loads((BASE / folder / 'summary.json').read_text(encoding='utf8'))
        assert summary['status'] == 'PASS'
        groups[folder] = []
        for run in summary['runs']:
            case = Path(run['fixture']['dataset']).parts[-3]
            directory = BASE / folder / case
            truth = json.loads((directory / 'held-out-truth.json').read_text(encoding='utf8'))
            positive = {tuple(p) for p in truth['positive_pairs']}
            pairs = json.loads((directory / run['variant'] / 'machine-pair-output.json').read_text(encoding='utf8'))
            # Map only for evaluation after outputs exist; never feed truth back.
            keys = {}
            for key in truth['entities']:
                source, identifier, _ = key.split(':')
                keys[source, identifier] = key
            def key_for(pair):
                return tuple(sorted(keys[pair[side+'_record']['source_system'], pair[side+'_record']['external_id']]
                                    for side in ('left','right')))
            clear = {key_for(pair) for pair in pairs if pair['baseline'].get('primary_group') == 'A'}
            machine = dict(run['machine_quality_before_truth_replay'])
            machine['rule_clear_group_A'] = metrics(clear, positive)
            if 'hard_blocked_truth_positive_pairs' in machine:
                machine['truth_positive_manual_merge_blocked'] = machine.pop('hard_blocked_truth_positive_pairs')
            completed = [pair for pair in pairs if (pair.get('advice') or {}).get('status') == 'COMPLETED']
            advice = Counter()
            for pair in completed:
                result = pair['advice']['recommendation']
                expected = key_for(pair) in positive
                advice['labeled_positive' if expected else 'labeled_negative'] += 1
                advice['abstained' if result == 'INSUFFICIENT_BASIS' else
                       'correct' if (result == 'LIKELY_SAME_PERSON') == expected else 'incorrect'] += 1
            machine['gemini_valid_advice_on_selected_pairs'] = dict(advice)
            row = {'case': case, 'variant': run['variant'], 'run_id': run['run_id'],
                'fixture': run['fixture'], 'profile_digest': run['profile_digest'],
                'machine_quality': machine, 'final_after_scripted_truth_review': run['final_quality_after_scripted_truth_review'],
                'benchmark_usage': run['telemetry'],
                'http_stages': run['http_stages'], 'report_path': (directory/run['variant']/'report.json').relative_to(ROOT).as_posix()}
            quality.append(row)
            groups[folder].append(run['run_id'])
    # The first UI smoke processed successfully but had an outdated heading
    # assertion. Its actual provider usage still belongs in the accounting.
    for directory in ('browser-01', 'browser-02'):
        path = BASE/directory/'report.json'
        if not path.exists():
            path = BASE/directory/'failure.json'
        run = json.loads(path.read_text(encoding='utf8'))
        groups['live-assistance-01'].append(run['run_id'])
    all_usage = {}
    for folder, identifiers in groups.items():
        credentials = json.loads((BASE/folder/'qa-access.private.json').read_text(encoding='utf8'))
        with httpx.Client(base_url='http://localhost:8001', headers={'Origin':'http://localhost:3000'}, timeout=90) as client:
            response = client.post('/api/v1/auth/login', json=credentials)
            response.raise_for_status()
            for identifier in identifiers:
                response = client.get(f'/api/v1/mdm/runs/{identifier}/workflow/telemetry')
                response.raise_for_status()
                all_usage[identifier] = response.json()
    totals = Counter()
    for usage in all_usage.values():
        for row in usage['embedding_by_position'].values():
            totals['embedding_http_attempts'] += row['http_attempts']
            totals['embedding_failed_responses'] += row['failed_responses']
            totals['embedding_unknown_usage_attempts'] += row['unknown_usage_attempts']
            totals['embedding_known_input_tokens'] += row['known_input_tokens']
        for row in usage['llm_by_purpose']:
            totals['gemini_http_attempts'] += row['http_attempts']
            totals['gemini_successes'] += row['successes']
            totals['gemini_known_input_tokens'] += row['known_input_tokens']
            totals['gemini_known_output_tokens'] += row['known_output_tokens']
            totals['gemini_unknown_usage_attempts'] += row['unknown_usage_attempts']
            totals['gemini_schema_invalid'] += row['statuses'].get('SCHEMA_INVALID', 0)
    save(BASE/'all-run-telemetry.json', {'runs':all_usage,'totals':dict(totals),
        'scope':'Seven truth-backed QA runs plus both browser smoke runs and one on-demand explanation on an existing run. No provider call is made by this reporting script.'})
    save(BASE/'comparison.json', {'runs':quality,'totals':dict(totals),
        'selected_default':'RULE_WITH_EMBEDDING_EVIDENCE',
        'limitations':['Synthetic diagnostic subsets, no independent human gold.',
            'Single runs, no p95 or load claim.',
            'Full retrieval universes differ; this is an end-to-end comparison, not a scorer-only ablation.',
            'Final cluster scores follow explicit scripted truth review and are not autonomous matching scores.']})
    print('CASE / MODE | RECORDS | CANDIDATES | CANDIDATE RECALL | PROCESS SECONDS | EMBEDDING CALLS | GEMINI CALLS | FINAL TP/FP/FN')
    for row in quality:
        usage = row['benchmark_usage']
        seconds = next(s['seconds'] for s in row['http_stages'] if s['stage']=='one-command-matching-advice-preview')
        embedding = sum(x['http_attempts'] for x in usage['embedding_by_position'].values())
        llm = sum(x['http_attempts'] for x in usage['llm_by_purpose'])
        final = row['final_after_scripted_truth_review']['pairwise']
        print(f"{row['case']} / {row['variant']} | {row['fixture']['record_count']} | {row['machine_quality']['candidate_count']} | {row['machine_quality']['candidate_recall']:.3f} | {seconds:.3f} | {embedding} | {llm} | {final['tp']}/{final['fp']}/{final['fn']}")
    print(json.dumps(dict(totals)))


if __name__ == '__main__':
    main()
