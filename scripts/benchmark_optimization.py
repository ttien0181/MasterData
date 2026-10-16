"""Offline, source-only diagnostic; evaluate held-out synthetic truth after retrieval.

No external APIs, profile activation, database writes or threshold selection.
"""
# ruff: noqa: E402 -- local source path and BLAS limits must precede numerical imports
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend/src')]
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'

import numpy as np
import pyarrow.parquet as pq
from masterone.mdm.matching_agents.candidate_union import build_union
from masterone.mdm.matching_agents.contracts import PairKey
from masterone.mdm.matching_agents.embedding_agent import EmbeddingAgent
from masterone.mdm.pipeline.standardization import standardize


def candidate_case(case, global_lexical, experiment='E1'):
    directory = ROOT / 'artifacts/full10k-20261016/run-02' / case
    fixture = json.loads((directory / 'fixture.json').read_text(encoding='utf8'))
    source = directory / 'source-only.csv'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fixture['source_sha256']
    records, external = [], {}
    with source.open(encoding='utf8', newline='') as stream:
        for raw in csv.DictReader(stream):
            rid = raw['source_system'] + ':' + raw['source_record_id']
            external[(raw['source_system'], raw['source_record_id'])] = rid
            records.append({'id': rid, 'entity_type': raw['entity_type'],
                            'normalized_fields': standardize(raw, entity_type=raw['entity_type'])[0]})
    cfg = {'experiment': experiment, 'retrieval': {'global_lexical': global_lexical,
        'query_batch_size': 64, 'target_batch_size': 256,
        'tfidf': {'top_k': 10, 'min_similarity': .30}},
        'execution_budget': {'unique_pairs': 300000, 'warning_pairs': 150000,
            'similarity_cells': 100000000, 'workspace_bytes': 512 * 1024**2, 'run_seconds': 1800}}
    started = time.perf_counter()
    output = build_union(records, cfg, None, [], lambda *_: None)
    seconds = time.perf_counter() - started
    # Truth is opened only after candidate generation completes.
    truth_path = ROOT / fixture['dataset'] / 'truth/records.parquet'
    truth_rows = pq.read_table(truth_path).to_pylist()
    truth = {}
    for row in truth_rows:
        key = (row['source_system'], row['source_record_id'])
        truth[external[key]] = row
    groups = defaultdict(list)
    for rid, row in truth.items():
        groups[row['entity_id']].append(rid)
    positive = {tuple(sorted(pair)) for members in groups.values() for pair in combinations(members, 2)}
    pairs = set(output.pairs)
    missing = positive - pairs
    normalized = {r['id']: r['normalized_fields'] for r in records}
    from masterone.mdm.pipeline.tfidf_blocking import _partition_key
    partitions = {r['id']: _partition_key(r) for r in records}
    result = {'records': len(records), 'source_sha256': fixture['source_sha256'],
        'truth_pairs': len(positive), 'candidate_count': len(pairs), 'seconds': seconds,
        'candidate_precision': len(pairs & positive) / len(pairs),
        'candidate_recall': len(pairs & positive) / len(positive),
        'missed_truth_pairs': len(missing),
        'passes': output.metrics['passes'], 'similarity_cells': output.metrics['similarity_cells'],
        'missed_different_year': sum(normalized[a].get('dob', '')[:4] != normalized[b].get('dob', '')[:4] for a, b in missing),
        'missed_different_legacy_partition': sum(partitions[a] != partitions[b] for a, b in missing),
        'by_type': {kind: {'truth_pairs': len(gold), 'found': len(gold & pairs),
                           'recall': len(gold & pairs) / len(gold)}
                    for kind in sorted({r['entity_type'] for r in truth.values()})
                    for gold in [{pair for pair in positive if truth[pair[0]]['entity_type'] == kind}]}}
    return result


def embedding_case():
    rng = np.random.default_rng(119)
    vectors = rng.normal(size=(400, 1536)).tolist()
    records = [{'id': f'{i:04}', 'normalized_fields': {'name_unicode': str(i),
        '_field_quality': {'name': 'VALID'}}} for i in range(400)]
    keys = [PairKey('owner', 'run', records[a]['id'], records[b]['id'])
            for a in range(400) for b in range(a + 1, 400)]
    def encode(texts, _):
        return [vectors[int(text)] for text in texts]
    cache = {}
    agent = EmbeddingAgent(encode, encoder_digest='benchmark', preprocessing_digest='v1',
        dimension=1536, batch_size=32, cache=cache, cache_version='text_vectors_v2')
    started = time.perf_counter()
    output = agent.evaluate(records, keys)
    cold = time.perf_counter() - started
    values = [row['fields']['name'].cosine for row in output]
    started = time.perf_counter()
    warm_output = agent.evaluate(records, keys)
    warm = time.perf_counter() - started
    assert values == [row['fields']['name'].cosine for row in warm_output]
    from masterone.mdm.matching_agents.contracts import cosine
    error = max(abs(values[index] - cosine(vectors[a], vectors[b], 1536).cosine)
                for index, (a, b) in enumerate(combinations(range(400), 2)) if index % 199 == 0)
    return {'pairs': len(keys), 'dimensions': 1536, 'cold_seconds': cold, 'warm_seconds': warm,
            'sum_cosines': sum(values), 'reference_max_abs_error': error, 'external_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--global-lexical', action='store_true')
    parser.add_argument('--embedding-only', action='store_true')
    parser.add_argument('--skip-embedding', action='store_true')
    parser.add_argument('--experiment', choices=('E0', 'E1'), default='E1')
    args = parser.parse_args()
    report = {'environment': 'Windows local offline; single BLAS thread; no Docker; not end-to-end',
              'global_lexical': args.global_lexical, 'experiment': args.experiment}
    if not args.skip_embedding:
        report['embedding'] = embedding_case()
    if not args.embedding_only:
        for case in ('TC1_easy_match', 'TC3_hard_match'):
            report[case] = candidate_case(case, args.global_lexical, args.experiment)
    import psutil
    memory = psutil.Process().memory_info()
    report['process_peak_working_set_bytes'] = getattr(memory, 'peak_wset', None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
