"""Offline, source-only candidate ablation. Truth is read after all production work.

Run each mode in a fresh process for comparable OS peak RSS. A snapshot can
preserve the record IDs and normalized input of a historical run. No providers,
review decisions, database writes or label features are used by this script.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import replace
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'scripts')]
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'MDM_SCORE_WORKERS'):
    os.environ[key] = '1'
os.environ['SEMANTIC_ADDRESS_ENABLED'] = 'false'


def metrics(predicted, positive):
    tp = len(predicted & positive)
    return {'tp': tp, 'fp': len(predicted - positive), 'fn': len(positive - predicted),
        'precision': tp / len(predicted) if predicted else None,
        'recall': tp / len(positive) if positive else None,
        'f1': 2 * tp / (len(predicted) + len(positive)) if predicted or positive else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--mode', choices=('baseline', 'normalization', 'identifiers', 'dob', 'v2', 'lexical'), required=True)
    parser.add_argument('--score', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from benchmark_sparse_tfidf import peak_rss
    from masterone.mdm.engine_adapter import CORE_FEATURES, engine_context
    from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine, CandidateResult
    from masterone.mdm.pipeline.standardization import fold_text, standardize
    from masterone.mdm.pipeline.tfidf_blocking import GlobalTfidfIndex
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text(encoding='utf8'))
        records = snapshot['records']
    else:
        if args.source is None:
            parser.error('--source or --snapshot is required')
        records = []
        with args.source.open(encoding='utf8', newline='') as stream:
            for raw in csv.DictReader(stream):
                nf, quality = standardize(raw, raw['entity_type'])
                key = raw['source_system'] + ':' + raw['source_record_id']
                records.append({'id': hashlib.sha256(key.encode()).hexdigest(), 'source_system': raw['source_system'],
                    'external_id': raw['source_record_id'], 'entity_type': raw['entity_type'],
                    'normalized_fields': nf, 'raw_fields': raw, 'quality_flags': quality})
    flags = {f: False for f in CORE_FEATURES}  # Same rule profile as Round 3.
    flags['candidate_recall_v2'] = args.mode in ('v2', 'dob')
    started = time.perf_counter()
    cfg = CandidateConfig(recall_v2=False)
    if args.mode == 'normalization':
        for r in records:
            nf = r['normalized_fields']
            nf['name'] = fold_text(nf.get('name') or '')
    if flags['candidate_recall_v2']:
        cfg = replace(cfg, recall_v2=True)
    result = CandidateEngine(cfg).generate(records)
    if args.mode == 'dob':
        projected = {p: sources - {'person_tokens_month_day'} for p, sources in result.pairs.items()}
        result = CandidateResult({p:s for p,s in projected.items() if s}, result.metrics, result.block_log)
    if args.mode == 'lexical':
        pairs = {p:set(s) for p,s in result.pairs.items()}
        for kind in sorted({r['entity_type'] for r in records}):
            rows = [r for r in records if r['entity_type'] == kind]
            index = GlobalTfidfIndex(rows)
            for a,b,_ in index.search():
                pairs.setdefault(tuple(sorted((a,b))), set()).add('tfidf_global_name')
        result = CandidateResult(pairs, result.metrics, result.block_log)
    retrieval_seconds = time.perf_counter() - started
    retrieval_peak = peak_rss()
    decisions = {}
    proposals = []
    score_seconds = None
    if args.score:
        from masterone.mdm.pipeline.clustering import build_proposals
        from masterone.mdm.pipeline.scoring_engine import ScoringConfig
        from masterone.mdm.resolution_service import _compute
        t = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='recall-v2-') as work, engine_context(flags):
            output = _compute(records, 'recall-offline', 'person-v3', integration_candidates=result,
                scoring_config=ScoringConfig(), scoring_output_dir=Path(work), _stop_after_decide=True)
            for pair in list(output['pairs']) + output['insufficient_pairs']:
                decisions[tuple(sorted((pair['left_id'],pair['right_id'])))] = {
                    k: pair.get(k) for k in ('decision', 'reason', 'primary_group', 'rule_score')}
            clustered = build_proposals(records, output['pairs'], 'recall-offline', 'person-v3')
            proposals = clustered['proposals']
            if hasattr(output['pairs'], 'close'):
                output['pairs'].close()
        score_seconds = time.perf_counter() - t
    production_seconds = time.perf_counter() - started
    production_peak = peak_rss()
    # Evaluation boundary. Neither retrieval nor SCORE/DECIDE sees truth.
    import pyarrow.parquet as pq
    lookup = {(r['source_system'],r['external_id']): r['id'] for r in records}
    by_id = {r['id']:r for r in records}
    groups = defaultdict(list)
    truth = pq.read_table(args.dataset / 'truth/records.parquet').to_pylist()
    for row in truth:
        if row['label_status'] == 'CERTAIN':
            groups[row['entity_id']].append(lookup[(row['source_system'],row['source_record_id'])])
    assert {rid for ids in groups.values() for rid in ids} == set(by_id), 'TRUTH_SOURCE_MISMATCH'
    gold = {tuple(sorted(p)) for ids in groups.values() for p in combinations(ids,2)}
    pairs = set(result.pairs)
    merged = {p for p,d in decisions.items() if d['decision'] == 'MERGEABLE'}
    clusters = {tuple(sorted(p)) for c in proposals for p in combinations(c['member_record_ids'],2)}
    degree = Counter(rid for p in pairs for rid in p)
    degrees = sorted(degree.get(rid,0) for rid in by_id)
    seen = set()
    passes = {}
    sources = sorted({s for value in result.pairs.values() for s in value}, key=lambda s: (s.startswith('person_'),s))
    for source in sources:
        found = {p for p,s in result.pairs.items() if source in s}
        added = found-seen
        passes[source] = {'candidates': len(found), 'overlap': len(found&seen), 'new_candidates':len(added),
            'true_pairs':len(found&gold), 'new_true_pairs':len(added&gold)}
        seen.update(found)
    historical = {tuple(sorted(p)) for p in snapshot['candidates']} if args.snapshot else set()
    gained = (pairs-historical)&gold if historical else set()
    report = {'mode':args.mode,'records':len(records),'truth_kind':'SYNTHETIC_GENERATOR',
        'truth_scope':'all CERTAIN same-entity combinations; includes policy-out-of-scope ORGANIZATION',
        'truth_used_during_pipeline':False,'provider_attempts':0,'flags':flags,'candidate_count':len(pairs),
        'candidate':metrics(pairs,gold),'retrieval_seconds':retrieval_seconds,'score_cluster_seconds':score_seconds,
        'production_seconds':production_seconds,'retrieval_peak_rss_bytes':retrieval_peak,'production_peak_rss_bytes':production_peak,
        'pairs_per_record':len(pairs)/len(records),'degree':{'mean':sum(degrees)/len(degrees),
            'p95':degrees[int(.95*(len(degrees)-1))],'max':max(degrees)},'passes':passes,
        'new_vs_historical':len(pairs-historical) if historical else None,
        'lost_vs_historical':len(historical-pairs) if historical else None,
        'additional_true_vs_historical':len(gained) if historical else None,
        'by_type':{kind:metrics({p for p in pairs if all(by_id[r]['entity_type']==kind for r in p)},
            {p for p in gold if all(by_id[r]['entity_type']==kind for r in p)}) for kind in ('PERSON','ORGANIZATION')},
        'pair':metrics(merged,gold) if args.score else None,'cluster_pairwise':metrics(clusters,gold) if args.score else None,
        'decisions':dict(Counter(d['decision'] for d in decisions.values())),
        'truth_decisions':dict(Counter(decisions[p]['decision'] for p in pairs&gold)) if args.score else None,
        'recovered_truth_decisions':dict(Counter(decisions[p]['decision'] for p in gained)) if args.score and historical else None,
        'scope':'offline rule proposals; REVIEW/GOLDEN/HTTP require a separate end-to-end run'}
    if args.score and historical:
        shared = sorted(pairs & historical)
        report['shared_historical_decisions'] = {'pairs':len(shared), 'sha256':hashlib.sha256(
            json.dumps([[a,b,decisions[(a,b)]] for a,b in shared],sort_keys=True).encode()).hexdigest()}
    report['source_code_sha256'] = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [Path(__file__).resolve(), *[ROOT/'backend/src/masterone/mdm'/name for name in (
            'pipeline/candidate_engine.py','pipeline/person_blocking.py','pipeline/tfidf_blocking.py',
            'pipeline/scoring_engine.py','pipeline/standardization.py','resolution_service.py','engine_adapter.py')]]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('mode','candidate_count','candidate','retrieval_seconds','production_seconds','production_peak_rss_bytes')}),flush=True)


if __name__ == '__main__':
    main()
