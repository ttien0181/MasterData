"""Source-only retrieval ablation; labels are opened strictly after production work.

Each trial runs in a fresh process. No provider calls, DB, truth replay, API
activation or tuning. Optional rule/guarded proposal metrics are diagnostics,
not FINALIZED Golden metrics. Keep raw receipts alongside aggregate summaries.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from importlib.metadata import version
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'scripts')]
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
os.environ['MDM_SCORE_WORKERS'] = '1'


def code_fingerprint():
    paths = [Path(__file__).resolve(),
        *sorted((ROOT / 'backend/src/masterone/mdm/matching_agents').glob('*.py')),
        *[ROOT / 'backend/src/masterone/mdm/pipeline' / name for name in
            ('standardization.py', 'scoring_engine.py', 'matching.py', 'clustering.py', 'decision_semantics.py')],
        *sorted((ROOT / 'backend/src/masterone/mdm/config').glob('*.yaml'))]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def metrics(predicted, positive):
    tp = len(predicted & positive)
    precision = tp / len(predicted) if predicted else None
    recall = tp / len(positive) if positive else None
    return {'precision': precision, 'recall': recall,
            'f1': 2 * tp / (len(predicted) + len(positive)) if predicted or positive else None,
            'overmerge_pairs': len(predicted - positive), 'undermerge_pairs': len(positive - predicted)}


def trial(case, mode, score):
    code_identity = code_fingerprint()
    import pyarrow.parquet as pq
    from benchmark_sparse_tfidf import peak_rss
    from masterone.mdm.engine_adapter import CORE_FEATURES, engine_context
    from masterone.mdm.matching_agents.candidate_union import build_union
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.pipeline.tfidf_blocking import _partition_key

    fixture = json.loads((case / 'fixture.json').read_text(encoding='utf8'))
    source = case / 'source-only.csv'
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha != fixture['source_sha256']:
        raise ValueError('SOURCE_HASH_MISMATCH')
    records = []
    with source.open(encoding='utf8', newline='') as stream:
        for raw in csv.DictReader(stream):
            fields, quality = standardize(raw, entity_type=raw['entity_type'])
            records.append({'id': raw['source_system'] + ':' + raw['source_record_id'],
                'external_id': raw['source_record_id'], 'source_system': raw['source_system'],
                'entity_type': raw['entity_type'], 'raw_fields': raw,
                'normalized_fields': fields, 'quality_flags': quality})
    cfg = {'experiment': 'E1' if mode in ('fallback', 'lexical') else 'E0',
        'retrieval': {'person_recovery': mode == 'recovery', 'global_lexical': mode == 'lexical',
            'query_batch_size': 64, 'target_batch_size': 256,
            'tfidf': {'top_k': 10, 'min_similarity': .30}},
        'execution_budget': {'unique_pairs': 200000, 'warning_pairs': 150000,
            'similarity_cells': 100000000, 'workspace_bytes': 512 * 1024**2, 'run_seconds': 1800}}
    started = time.perf_counter()
    cpu = time.process_time()
    output = build_union(records, cfg, None, [], lambda *_: None)
    retrieval_seconds = time.perf_counter() - started
    predicted = set()
    decisions = None
    decision_index = {}
    score_seconds = None
    membership = []
    if score:
        from masterone.mdm.pipeline.clustering import build_proposals
        from masterone.mdm.pipeline.scoring_engine import ScoringConfig
        from masterone.mdm.resolution_service import _compute
        score_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='p119-offline-') as workspace:
            with engine_context({flag: True for flag in CORE_FEATURES}):
                result = _compute(records, 'offline-diagnostic', 'person-v3', integration_candidates=output,
                    scoring_config=ScoringConfig(), scoring_output_dir=Path(workspace), _stop_after_decide=True)
                decisions = dict(Counter(p['decision'] for p in result['pairs']))
                decisions['INSUFFICIENT_EVIDENCE'] = result['insufficient_count']
                for collection in (result['pairs'], result['insufficient_pairs']):
                    for pair in collection:
                        decision_index[tuple(sorted((pair['left_id'], pair['right_id'])))] = (pair['decision'], pair.get('reason'))
                clustered = build_proposals(records, result['pairs'], 'offline-diagnostic', 'person-v3')
                membership = [p['member_record_ids'] for p in clustered['proposals']]
                predicted = {tuple(sorted(pair)) for members in membership for pair in combinations(members, 2)}
                if hasattr(result['pairs'], 'close'):
                    result['pairs'].close()
        score_seconds = time.perf_counter() - score_started
    production_seconds = time.perf_counter() - started
    production_cpu = time.process_time() - cpu
    # Evaluation boundary: labels cannot influence retrieval/scoring/clustering.
    groups = defaultdict(list)
    truth_path = ROOT / fixture['dataset'] / 'truth/records.parquet'
    truth_sha = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    for batch in pq.ParquetFile(truth_path).iter_batches(columns=['entity_id', 'source_system', 'source_record_id']):
        for row in batch.to_pylist():
            groups[row['entity_id']].append(row['source_system'] + ':' + row['source_record_id'])
    positive = {tuple(sorted(pair)) for members in groups.values() for pair in combinations(members, 2)}
    index = {r['id']: r for r in records}
    if set(index) != {rid for members in groups.values() for rid in members}:
        raise ValueError('TRUTH_SOURCE_IDENTITY_MISMATCH')
    pairs = set(output.pairs)
    missed = positive - pairs
    reasons = Counter()
    for a, b in missed:
        x, y = index[a]['normalized_fields'], index[b]['normalized_fields']
        reasons['different_legacy_partition'] += _partition_key(index[a]) != _partition_key(index[b])
        reasons['same_valid_full_dob'] += bool(x.get('dob') == y.get('dob') and x.get('_field_quality', {}).get('dob') == 'VALID'
            and y.get('_field_quality', {}).get('dob') == 'VALID')
        reasons['same_name_token_set'] += set(x.get('name', '').split()) == set(y.get('name', '').split())
        reasons['missing_name_or_address'] += any(not nf.get(f) for nf in (x, y) for f in ('name', 'address'))
    seen = set()
    passes = {}
    for source_name in output.metrics['pass_order']:
        found = {pair for pair, sources in output.pairs.items() if source_name in sources}
        passes[source_name] = {'candidates': len(found), 'new_candidates': len(found - seen),
            'true_pairs': len(found & positive), 'new_true_pairs': len((found - seen) & positive)}
        seen.update(found)
    merged_ids = [rid for members in membership for rid in members]
    if len(merged_ids) != len(set(merged_ids)) or not set(merged_ids) <= set(index):
        raise ValueError('PROPOSAL_RECORD_CONSERVATION_FAILED')
    return {'status': 'COMPLETE', 'mode': mode, 'case': case.name, 'source_sha256': source_sha, 'truth_sha256': truth_sha,
        'source_code_sha256': code_identity,
        'config': cfg, 'records': len(records), 'truth_pairs': len(positive),
        'rule_profile': {'policy_version': 'person-v3', 'engine_flags': {flag: True for flag in CORE_FEATURES},
                         'mode': 'rule', 'workers': 1} if score else None,
        'candidates': len(pairs), 'candidate_recall': len(pairs & positive) / len(positive),
        'candidate_precision': len(pairs & positive) / len(pairs) if pairs else None,
        'candidate_reduction_ratio': 1 - len(pairs) / (len(records) * (len(records) - 1) / 2),
        'retrieval_seconds': retrieval_seconds, 'score_cluster_seconds': score_seconds,
        'production_seconds': production_seconds, 'production_cpu_seconds': production_cpu,
        'missed_pairs': len(missed), 'missed_diagnostics_overlapping': dict(reasons), 'passes': passes,
        'by_type': {kind: {'truth_pairs': len(gold), 'found': len(gold & pairs), 'recall': len(gold & pairs) / len(gold)}
            for kind in sorted({r['entity_type'] for r in records})
            for gold in [{pair for pair in positive if index[pair[0]]['entity_type'] == kind}]},
        'proposal_metrics_without_truth': metrics(predicted, positive) if score else None,
        'decisions': decisions, 'record_conservation_including_singletons': True if score else None,
        'truth_pair_decisions': dict(Counter(decision_index[p][0] for p in positive & pairs)) if score else None,
        'recovery_truth_pair_reasons': dict(Counter(str(decision_index[p]) for p in positive & pairs
            if any(s.startswith('person_') for s in output.pairs[p]))) if score else None,
        'provider_attempts': 0, 'process_peak_rss_bytes_including_evaluation': peak_rss(),
        'scope': 'offline production rule/guarded proposals; no HTTP/FINALIZED/provider/coordinator/auto-merge',
        'person_recovery': {k: v for k, v in output.metrics.get('person_recovery', {}).items() if k != 'blocks'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True, help='Directory of case folders containing source-only.csv and fixture.json')
    parser.add_argument('--modes', nargs='+', choices=('legacy', 'recovery', 'fallback', 'lexical'), default=['legacy', 'recovery'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--score', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if args.worker:
        result = trial(args.cases.resolve(), args.modes[0], args.score)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
        return
    args.output.mkdir(parents=True, exist_ok=True)
    expected_code = code_fingerprint()
    receipts = []
    for case in sorted(args.cases.iterdir()):
        if not (case / 'fixture.json').is_file() or not (case / 'source-only.csv').is_file():
            continue
        for mode in args.modes:
            for repeat in range(args.repeats):
                target = args.output / f'{case.name}-{mode}-{repeat+1}.json'
                cmd = [sys.executable, '-X', 'utf8', __file__, '--worker', '--cases', str(case), '--modes', mode, '--output', str(target)]
                if args.score:
                    cmd.append('--score')
                completed = subprocess.run(cmd, capture_output=True, encoding='utf8')
                if completed.returncode:
                    result = {'case': case.name, 'mode': mode, 'status': 'FAILED_OR_GUARDED', 'stderr': completed.stderr,
                              'returncode': completed.returncode}
                    target.write_text(json.dumps(result, indent=2), encoding='utf8')
                else:
                    result = json.loads(target.read_text(encoding='utf8'))
                receipts.append(result)
                print(json.dumps({k: result.get(k) for k in ('case', 'mode', 'status', 'candidates', 'candidate_recall', 'retrieval_seconds', 'score_cluster_seconds')}), flush=True)
    summary = {'environment': {'python': sys.version, 'platform': sys.platform, 'blas_threads': 1,
        'dependencies': {package: version(package) for package in
            ('scikit-learn', 'numpy', 'pyarrow', 'duckdb', 'rapidfuzz', 'psutil')},
        'cache': 'fresh Python process; OS/file cache uncontrolled; no embedding cache or provider'},
        'repeats': args.repeats, 'receipts': receipts,
        'source_code_sha256': expected_code,
        'aggregates': []}
    for case in sorted({r['case'] for r in receipts}):
        for mode in args.modes:
            rows = [r for r in receipts if r['case'] == case and r['mode'] == mode and 'retrieval_seconds' in r]
            if rows:
                times = sorted(r['retrieval_seconds'] for r in rows)
                summary['aggregates'].append({'case': case, 'mode': mode, 'n': len(rows),
                    'median_retrieval_seconds': statistics.median(times),
                    'empirical_p95_seconds_nearest_rank': times[max(0, math.ceil(.95 * len(times)) - 1)],
                    'candidates': sorted({r['candidates'] for r in rows}),
                    'candidate_recall': sorted({r['candidate_recall'] for r in rows}),
                    'note': 'Three trials are insufficient to estimate tail latency reliably.'})
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    if any(r.get('status') == 'FAILED_OR_GUARDED' for r in receipts):
        raise SystemExit(1)
    if any(r['source_code_sha256'] != expected_code for r in receipts) or code_fingerprint() != expected_code:
        raise SystemExit('CODE_CHANGED_DURING_BENCHMARK')


if __name__ == '__main__':
    main()
