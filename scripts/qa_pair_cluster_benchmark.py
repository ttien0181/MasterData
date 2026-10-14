"""Bounded offline diagnostic for mentor bundles; never enables automatic merge.

Input is selected by raw source identity, independently of truth. Incomplete
bundles and capped samples are explicitly partial; truth is opened after compute.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def integrity(bundle):
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    missing, mismatches, checked = [], [], 0
    for relative, expected in manifest['file_hashes'].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle.resolve()):
            raise ValueError('Manifest path escapes bundle')
        if not path.exists():
            missing.append(relative)
            continue
        checked += 1
        actual = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        if actual != expected:
            mismatches.append({'file': relative, 'expected': expected, 'actual': actual})
    return {'target_records': manifest['target_records'], 'manifest_data_hash': manifest['data_hash'] if 'data_hash' in manifest else None,
            'files_checked': checked, 'missing_files': missing, 'hash_mismatches': mismatches,
            'complete_hash_match': not missing and not mismatches}


def load_raw(bundle, cap):
    records, keys, counts = [], {}, Counter()
    for path in sorted((bundle / 'input_p119').glob('*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream, strict=True):
                key = row['source_system'] + ':' + row['source_record_id']
                rid = hashlib.sha256(key.encode()).hexdigest()
                if rid in keys:
                    raise ValueError('Duplicate source identity')
                keys[rid] = key
                records.append({'id': rid, 'external_id': row['source_record_id'],
                    'source_system': row['source_system'], 'entity_type': row['entity_type'],
                    'raw_fields': dict(row)})
                counts[path.name] += 1
    available = len(records)
    records.sort(key=lambda record: record['id'])
    records = records[:cap]
    keys = {record['id']: keys[record['id']] for record in records}
    return records, keys, {'available_records': available, 'loaded_records': len(records),
        'file_counts': dict(counts), 'sampling': 'smallest SHA256(source_system:source_record_id), no truth read'}


def quality(truth, candidates, clusters):
    owners = defaultdict(list)
    for rid, row in truth.items():
        if row['label_status'] == 'CERTAIN' and row['entity_id']:
            owners[row['entity_id']].append(rid)
    positives = {tuple(sorted(pair)) for members in owners.values() for pair in combinations(members, 2)}
    candidates = {tuple(sorted(pair)) for pair in candidates}
    merged = {tuple(sorted(pair)) for members in clusters for pair in combinations(members, 2)}
    if any(rid not in truth for pair in merged for rid in pair):
        raise ValueError('Unlabelled proposal endpoint')
    junk = {pair for pair in merged if any(truth[rid]['label_status'] == 'JUNK' for rid in pair)}
    tp = len(merged & positives)
    return {'known_positive_pairs_in_loaded_subset': len(positives),
        'candidate_true_pairs': len(candidates & positives),
        'candidate_recall_in_loaded_subset': len(candidates & positives) / len(positives) if positives else None,
        'cluster_true_pairs': tp, 'cluster_false_pairs': len(merged - positives),
        'junk_co_cluster_pairs': len(junk),
        'cluster_pair_precision': tp / len(merged) if merged else None,
        'cluster_pair_recall_in_loaded_subset': tp / len(positives) if positives else None,
        'label_status_counts': dict(Counter(row['label_status'] for row in truth.values()))}


def benchmark(bundle, output, cap):
    import psutil
    import pyarrow.parquet as pq
    sys.path.insert(0, str(ROOT / 'backend/src'))
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig
    from masterone.mdm.pipeline.stage_runner import StageRunner

    check = integrity(bundle)
    records, keys, raw_stats = load_raw(bundle, cap)
    (output / 'input_integrity.json').write_text(json.dumps(check, indent=2), encoding='utf-8')
    process, stopped, peak = psutil.Process(), threading.Event(), [process_rss := psutil.Process().memory_info().rss]
    def monitor():
        while not stopped.wait(.05):
            peak[0] = max(peak[0], process.memory_info().rss)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    runner = StageRunner('pair-cluster-bench', 'offline', output_dir=output)
    started, cpu = time.monotonic(), time.process_time()
    try:
        with engine_context({'decision_semantics_v2_enabled': True, 'cluster_safety_v2_enabled': True,
                             'generation_staging_enabled': False}):
            result = runner.run_all(records, candidate_config=CandidateConfig(max_unique_pairs=1000000))
        seconds, cpu_seconds = time.monotonic() - started, time.process_time() - cpu
        # Evaluation begins only here; this mapping is never fed into compute.
        rows = pq.read_table(bundle / 'truth/records.parquet').to_pylist()
        truth_by_key = {row['record_key']: row for row in rows}
        truth = {rid: truth_by_key[key] for rid, key in keys.items()}
        pairs = list(result['pairs'])
        candidates = [(row['left_id'], row['right_id']) for row in
            pq.read_table(output / 'pair-cluster-bench_candidates.parquet').to_pylist()]
        clusters = [p['member_record_ids'] for p in result['proposals']]
        measured = {'status': 'PARTIAL_DIAGNOSTIC' if not check['complete_hash_match'] or len(records) != check['target_records'] else 'FULL_OFFLINE_EXECUTION',
            'case': bundle.parents[1].name, 'integrity': check, 'raw': raw_stats,
            'seconds': seconds, 'cpu_seconds': cpu_seconds, 'peak_sampled_rss_bytes': peak[0],
            'rss_before_pipeline_bytes': process_rss,
            'candidate_count': result['candidate_count'], 'serving_pairs': len(pairs),
            'proposals': len(clusters), 'review_cases': len(result['review_cases']),
            'stages': [asdict(stage) for stage in runner.stage_results],
            'candidate_metrics': result.get('candidate_metrics'), 'cluster_metrics': result.get('cluster_metrics'),
            'quality': quality(truth, candidates, clusters),
            'decisions': dict(Counter(pair['decision'] for pair in pairs)),
            'triage_reasons': dict(Counter(pair.get('triage_reason') for pair in pairs)),
            'cluster_member_count': sum(map(len, clusters)),
            'strata_in_loaded_subset': dict(Counter(row['stratum'] for row in truth.values())),
            'llm': {'enabled': False, 'calls': 0, 'tokens': 0, 'cost': 0},
            'automatic_merge_enabled': False, 'truth_used_during_pipeline': False,
            'profile': 'decision_semantics_v2 + cluster_safety_v2; remaining defaults; candidate fail-closed cap 1M',
            'limitations': ['Offline StageRunner; no publication/intake/API/UI performance measured',
                'Synthetic PRIOR/L0; no production quality calibration',
                'Sample selection changes block sizes/frequencies; subset recall is not full-bundle recall',
                'Review/cannot-link replay plans are not automatically injected from truth',
                'Proposal ownership evaluated; proposal generation is not automatic merge execution']}
        (output / 'measurement.json').write_text(json.dumps(measured, indent=2, default=str), encoding='utf-8')
        print(json.dumps({k: measured[k] for k in ('status', 'case', 'seconds', 'candidate_count', 'proposals', 'quality')}, indent=2), flush=True)
    finally:
        stopped.set()
        thread.join()
        runner.close_decisions()
        runner.con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cap', type=int, default=12000)
    parser.add_argument('--integrity-only', action='store_true')
    args = parser.parse_args()
    if args.bundle.parent.name != '100k' or args.cap < 1 or args.cap > 100000:
        parser.error('Only bounded 100k bundles are allowed; 1M is excluded')
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_merge_baseline import isolated_environment
    os.environ.update(isolated_environment(args.output / 'environment'))
    os.environ['PAIR_ASSESSMENT_AUTO_ENABLED'] = 'false'
    os.environ['LLM_PAIR_ASSESSMENT_ENABLED'] = 'false'
    if args.integrity_only:
        result = integrity(args.bundle)
        (args.output / 'input_integrity.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    else:
        benchmark(args.bundle, args.output, args.cap)


if __name__ == '__main__':
    main()
