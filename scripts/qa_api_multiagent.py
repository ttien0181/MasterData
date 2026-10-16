"""Real Docker HTTP/queue QA on hash-verified Drive subsets with separate truth.

No business mocks, direct DB decision writes, model training or automatic merges.
Truth replay is explicitly scripted QA and is not autonomous matching accuracy.
Only the newly registered QA actor is provisioned in the existing local QA DB.
"""
import argparse
import csv
import hashlib
import io
import json
import secrets
import subprocess
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from uuid import uuid4

import httpx
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['source_system', 'source_record_id', 'entity_type', 'name', 'phone', 'email',
          'dob', 'address', 'government_id', 'government_id_type', 'source_updated_at', 'is_active']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def fixture(dataset, output, entities=20, negatives=10):
    manifest = yaml.safe_load((dataset / 'manifest.yaml').read_text(encoding='utf8'))
    for relative, expected in manifest['file_hashes'].items():
        path = (dataset / relative).resolve()
        assert path.is_relative_to(dataset.resolve()) and sha(path) == expected, 'SOURCE_HASH_MISMATCH'
    mappings = yaml.safe_load((dataset / 'raw/source_mapping.yaml').read_text(encoding='utf8'))['files']
    rows = {}
    for mapping in mappings:
        with (dataset / mapping['path']).open(encoding=mapping['encoding'], newline='') as handle:
            for source in csv.DictReader(handle, delimiter=mapping['delimiter']):
                row = {key: source.get(mapping['columns'].get(key, key), '') for key in FIELDS}
                key = f"{row['source_system']}:{row['source_record_id']}:{mapping['batch_id']}"
                assert key not in rows, 'DUPLICATE_SOURCE_KEY'
                rows[key] = row
    truth = {r['record_key']: r for r in pq.read_table(dataset / 'truth/records.parquet').to_pylist()
             if r['entity_type'] == 'PERSON' and r['label_status'] == 'CERTAIN'}
    groups = defaultdict(list)
    for key, row in truth.items():
        if key in rows:
            groups[row['entity_id']].append(key)
    labels = pq.read_table(dataset / 'truth/answer_keys/evaluation_pairs.parquet').to_pylist()
    # Selection is deterministic and independent of model outputs or thresholds.
    ordered = sorted(groups, key=lambda entity: hashlib.sha256(entity.encode()).hexdigest())
    selected = set(next_entity for next_entity in ordered if len(groups[next_entity]) > 1)
    chosen_entities = set([entity for entity in ordered if entity in selected][:entities])
    controls = [r for r in labels if r['label'] == 'NON_MATCH' and r['record_key_a'] in truth and r['record_key_b'] in truth]
    for pair in sorted(controls, key=lambda r: hashlib.sha256((r['record_key_a'] + r['record_key_b']).encode()).hexdigest())[:negatives]:
        chosen_entities.update(truth[pair[key]]['entity_id'] for key in ('record_key_a', 'record_key_b'))
    chosen = sorted(key for entity in chosen_entities for key in groups[entity])
    identities = [(rows[key]['source_system'], rows[key]['source_record_id']) for key in chosen]
    assert len(identities) == len(set(identities)), 'UPLOAD_REQUIRES_DISTINCT_SOURCE_IDS_ACROSS_SNAPSHOTS'
    assert len(chosen) <= 200, 'SMALL_QA_LIMIT'
    source_path = output / 'source-only.csv'
    with source_path.open('w', encoding='utf8', newline='') as handle:
        writer = csv.DictWriter(handle, FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows[key] for key in chosen)
    gold = {key: truth[key]['entity_id'] for key in chosen}
    positives = {tuple(sorted((a, b))) for keys in groups.values() for a, b in combinations(keys, 2)
                 if a in gold and b in gold}
    receipt = {'dataset': dataset.relative_to(ROOT).as_posix(), 'manifest_sha256': sha(dataset / 'manifest.yaml'),
        'source_hashes_verified': len(manifest['file_hashes']), 'fixture_sha256': sha(source_path),
        'record_count': len(chosen), 'entity_count': len(chosen_entities), 'entity_type': 'PERSON',
        'positive_pairs': len(positives), 'all_entity_negative_pairs': len(chosen)*(len(chosen)-1)//2-len(positives),
        'truth_kind': 'SYNTHETIC_GENERATOR_NOT_INDEPENDENT_HUMAN_GOLD',
        'selection': 'First hashed complete multi-record PERSON entities plus hashed labeled negative controls; not selected by model outcomes.',
        'batch_counts': dict(Counter(truth[key]['batch_id'] for key in chosen)),
        'selected_evaluation_labels': dict(Counter(r['label'] for r in labels if r['record_key_a'] in gold and r['record_key_b'] in gold))}
    save(output / 'fixture-manifest.json', receipt)
    save(output / 'held-out-truth.json', {'entities': gold, 'positive_pairs': sorted(positives)})
    # This mapping is only for evaluating outputs; never passed to application APIs.
    by_external = {(rows[key]['source_system'], rows[key]['source_record_id']): key for key in chosen}
    return source_path, receipt, gold, positives, by_external


def metrics(predicted, positive):
    tp = len(predicted & positive)
    fp, fn = len(predicted - positive), len(positive - predicted)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def docker_python(code, *, service='backend'):
    if service not in ('backend', 'worker'):
        raise ValueError('UNSUPPORTED_QA_SERVICE')
    result = subprocess.run(['docker', 'compose', 'exec', '-T', service, 'python', '-'], input=code,
        capture_output=True, text=True, encoding='utf8', cwd=ROOT, check=True)
    return json.loads(result.stdout)


def provision(actor):
    # The actor ID comes from registration; the runtime must remain the existing QA DB.
    return docker_python('''
import json
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from masterone.config import get_settings
from masterone.auth.models import User
from masterone.mdm.models import LLMPolicyControl
s=get_settings()
assert s.app_env=='test' and make_url(s.active_mdm_database_url).host=='database'
with Session(create_engine(s.active_mdm_database_url)) as db:
    actor=db.get(User, ACTOR)
    assert actor is not None and actor.email_normalized.startswith('api-multiagent-')
    actor.role='STEWARD'
    control=db.get(LLMPolicyControl,'global')
    assert control and control.enabled, 'Existing LLM policy switch must already be enabled'
    db.commit()
print(json.dumps({'actor_provisioned':True,'policy_switch_already_enabled':True}))
'''.replace('ACTOR', repr(actor)))


def checked(response):
    if response.status_code not in (200, 201, 202):
        # FastAPI application codes only; never provider bodies or request credentials.
        try:
            detail = response.json().get('detail')
        except ValueError:
            detail = 'NON_JSON_ERROR'
        raise RuntimeError(f'HTTP_{response.status_code}: {str(detail)[:350]}')
    return response.json()


def run_case(client, output, source, manifest, gold, positives, external, *, variant, advice_limit,
             model_scope='synthetic-benchmark', explanation_samples=0):
    case_started = time.perf_counter()
    models = variant != 'rule'
    report = {'status': 'RUNNING', 'variant': variant, 'fixture': manifest,
              'http_stages': [], 'review_replay': 'SCRIPTED_SYNTHETIC_QA'}
    save(output / 'report.json', report)
    def timed(name, fn):
        start = time.perf_counter()
        value = fn()
        report['http_stages'].append({'stage': name, 'seconds': time.perf_counter()-start})
        save(output / 'report.json', report)
        print(name, round(time.perf_counter()-start, 3), flush=True)
        return value
    run = timed('upload', lambda: checked(client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
        files={'files': ('source-only.csv', source.read_bytes(), 'text/csv')})))['run_id']
    report['run_id'] = run
    save(output / 'report.json', report)
    base = f'/api/v1/mdm/runs/{run}'
    workflow = base + '/workflow'
    def state():
        return checked(client.get(workflow))
    def body(**extra):
        return {'request_id': str(uuid4()), 'expected_workflow_revision': state()['workflow_revision'], **extra}
    def idle(expected):
        deadline = time.monotonic()+600
        while time.monotonic() < deadline:
            current = state()
            if current.get('retryable_operation'):
                raise RuntimeError('WORKER_FAILED_' + str(current['retryable_operation'].get('error_code')))
            if current['workflow_state'] == expected and not current.get('active_operation'):
                return current
            time.sleep(.4)
        raise TimeoutError('WORKER_STATE_TIMEOUT_' + expected)
    def pages(suffix, key='items', limit=100):
        items, offset = [], 0
        while True:
            page = checked(client.get(workflow+suffix, params={'limit': limit, 'offset': offset}))
            chunk = page[key]
            items.extend(chunk)
            if not chunk or not page.get('next_cursor'):
                return items
            offset += len(chunk)
    def collect_metrics():
        pairs = pages('/pairs')
        record_keys = {}
        for pair in pairs:
            for side in ('left', 'right'):
                record = pair[side+'_record']
                record_keys[record['record_id']] = external[(record['source_system'], record['external_id'])]
        candidate_keys = {tuple(sorted((record_keys[p['baseline']['left_id']], record_keys[p['baseline']['right_id']]))) for p in pairs}
        def predicted(selector):
            return {tuple(sorted((record_keys[p['baseline']['left_id']], record_keys[p['baseline']['right_id']])))
                    for p in pairs if selector(p)}
        def assessment(p):
            return p.get('evaluation') or p.get('assessment') or p['baseline']
        rule = predicted(lambda p: assessment(p).get('rule_conclusion', assessment(p).get('decision')) == 'MERGEABLE')
        clear = predicted(lambda p: assessment(p).get('primary_group') == 'A')
        soft = predicted(lambda p: assessment(p).get('soft_evaluation', {}).get('recommendation') == 'LIKELY_SAME_PERSON')
        gemini = predicted(lambda p: (p.get('advice') or {}).get('recommendation') == 'LIKELY_SAME_PERSON')
        report['machine_quality_before_truth_replay'] = {
            'candidate_recall': len(candidate_keys & positives)/len(positives) if positives else None,
            'candidate_count': len(pairs), 'all_pairs': len(gold)*(len(gold)-1)//2,
            'candidate_reduction_ratio': 1-len(pairs)/(len(gold)*(len(gold)-1)//2),
            'rule_recommendations': metrics(rule, positives), 'splink_recommendations': metrics(soft, positives),
            'rule_clear_group_A': metrics(clear, positives),
            'rule_conclusion_counts': dict(Counter(assessment(p).get('rule_conclusion') for p in pairs)),
            'gemini_selected_positive_recommendations': metrics(gemini, positives),
            'groups': dict(Counter(assessment(p).get('primary_group') for p in pairs)),
            'truth_positive_manual_merge_blocked': sum(
                tuple(sorted((record_keys[p['baseline']['left_id']],record_keys[p['baseline']['right_id']]))) in positives
                and not p['eligibility']['manual_allowed'] for p in pairs),
            'automatic_decisions': sum(p['decision'] != 'UNDECIDED' for p in pairs),
            'notes': ['Recommendation metrics are diagnostic; none of these sets authorizes a merge.',
                      'Splink and Gemini raw recommendations may contradict constraints; final routing still enforces them.',
                      'Gemini sees only selected ambiguous pairs, so its full-universe recall is not a standalone model score.']}
        save(output / 'machine-pair-output.json', pairs)
        return pairs, record_keys
    try:
        timed('prepare-worker-wait', lambda: idle('DATA_READY'))
        process = checked(client.post(workflow+'/process', json=body(
            model_scope=model_scope if models else None, include_advice=models, advice_limit=advice_limit)))
        report['processing_operation_id'] = process['operation_id']
        timed('one-command-matching-advice-preview', lambda: idle('DECIDE_REVIEW'))
        pairs, record_keys = timed('offline-quality-evaluation', collect_metrics)
        assert report['machine_quality_before_truth_replay']['automatic_decisions'] == 0
        save(output/'telemetry-before-review.json', checked(client.get(workflow+'/telemetry')))
        save(output/'report.json', report)

        def explanations():
            samples = []
            # Output-independent selection; do not use truth to pick good answers.
            for pair in sorted(pairs, key=lambda p: p['pair_id'])[:explanation_samples]:
                started = time.perf_counter()
                value = checked(client.post(workflow+f"/pairs/{pair['pair_id']}/explanation", json=body()))
                samples.append({'pair_id': pair['pair_id'], 'seconds': time.perf_counter()-started,
                                'status': value.get('status'), 'error_code': value.get('error_code')})
            report['explanation_samples'] = samples
        if models and explanation_samples:
            timed('api-explanation-samples', explanations)

        def replay_truth():
            decisions = Counter()
            negatives, accepted = [], defaultdict(set)
            for pair in pairs:
                key = tuple(sorted((record_keys[pair['baseline']['left_id']],record_keys[pair['baseline']['right_id']])))
                decision = 'merge' if key in positives and pair['eligibility']['manual_allowed'] else 'no_merge'
                if key in positives and decision != 'merge':
                    decisions['truth_positive_blocked_by_constraints'] += 1
                if decision == 'no_merge':
                    negatives.append(pair['pair_id'])
                else:
                    checked(client.post(workflow+f"/pairs/{pair['pair_id']}/decision", json=body(
                        expected_pair_revision=pair['revision'], decision=decision,
                        reason='Scripted replay of synthetic source truth for QA; not independent human gold')))
                    accepted[gold[key[0]]].update([pair['baseline']['left_id'], pair['baseline']['right_id']])
                decisions[decision] += 1
            for start in range(0, len(negatives), 1000):
                checked(client.post(workflow+'/bulk-decisions', json=body(
                    expected_decision_revision=state()['decision_revision'], decision='no_merge',
                    scope={'pair_ids':negatives[start:start+1000]},
                    reason='Scripted synthetic truth replay; preserves hard constraints')))
                idle('DECIDE_REVIEW')
            for members in accepted.values():
                checked(client.post(workflow+'/group-approvals', json=body(
                    expected_decision_revision=state()['decision_revision'], record_ids=sorted(members),
                    reason='Explicit scripted QA approval of a synthetic truth entity; not an automatic decision')))
            report['scripted_review_decisions'] = dict(decisions)
        timed('scripted-truth-review-http', replay_truth)
        checked(client.post(workflow+'/finalize-decisions', json=body(expected_decision_revision=state()['decision_revision'])))
        timed('cluster-worker-wait', lambda: idle('GOLDEN_REVIEW'))
        clusters = pages('/clusters')
        # Include singleton IDs not seen in candidate endpoints via the bounded data read.
        for offset in range(0, len(gold), 100):
            data = checked(client.get(workflow+'/data', params={'limit': 100, 'offset': offset}))
            for record in data['samples']['items']:
                record_keys[record['record_id']] = external[(record['source_system'],record['external_id'])]
        memberships = [rid for group in clusters for rid in group['member_record_ids']]
        assert len(memberships) == len(set(memberships)) == len(gold), 'MEMBERSHIP_INTEGRITY'
        cluster_pairs = {tuple(sorted((record_keys[a], record_keys[b]))) for group in clusters
                         for a,b in combinations(group['member_record_ids'],2)}
        report['final_quality_after_scripted_truth_review'] = {'pairwise': metrics(cluster_pairs, positives),
            'clusters': len(clusters), 'records_preserved': len(memberships),
            'overmerge_pairs': len(cluster_pairs-positives), 'undermerge_pairs': len(positives-cluster_pairs)}
        def golden_export():
            current = state()
            checked(client.post(workflow+'/golden/confirm-selected', json=body(
                expected_cluster_revision=current['cluster_revision'], expected_golden_revision=current['golden_revision'])))
            checked(client.post(workflow+'/finalize-golden', json=body(expected_golden_revision=state()['golden_revision'])))
            idle('FINALIZED')
            preview = checked(client.post(base+'/previews', json={'choices': {}, 'acknowledge_unreviewed': False}))
            exported = client.post(base+f"/previews/{preview['preview_id']}/execute", json={'execute_request_id':str(uuid4())})
            assert exported.status_code == 200, 'EXPORT_FAILED'
            rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
            assert sum(int(r['record_count']) for r in rows) == len(gold), 'EXPORT_LOST_RECORDS'
            assert all(r['master_record_id'] for r in rows), 'EXPORT_ID_MISSING'
            (output/'export.csv').write_bytes(exported.content)
            report['export'] = {'rows':len(rows),'record_count':len(gold),'sha256':sha(output/'export.csv')}
        timed('golden-confirm-finalize-export', golden_export)
        report['telemetry'] = checked(client.get(workflow+'/telemetry'))
        report['status'] = 'PASS'
    except Exception as error:
        report['status'] = 'FAIL'
        report['error'] = {'type':type(error).__name__,'code':str(error)[:400]}
        try:
            report['telemetry'] = checked(client.get(workflow+'/telemetry'))
        except Exception:
            pass
        raise
    finally:
        report['client_total_seconds'] = time.perf_counter() - case_started
        save(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, action='append', required=True)
    parser.add_argument('--entities', type=int, default=20)
    parser.add_argument('--negative-controls', type=int, default=10)
    parser.add_argument('--advice-limit', type=int, default=8)
    parser.add_argument('--explanation-samples', type=int, choices=range(0, 4), default=0)
    parser.add_argument('--variants', nargs='+', choices=['rule','evidence','hybrid'], default=['rule','hybrid'])
    parser.add_argument('--model-scope', choices=['synthetic-benchmark','none'], default='synthetic-benchmark')
    args=parser.parse_args()
    if not 1 <= args.entities <= 20 or not 0 <= args.negative_controls <= 10 or not 0 <= args.advice_limit <= 8:
        parser.error('Small QA requires entities 1..20, negative controls 0..10, advice limit 0..8')
    out=args.output.resolve()
    assert out.is_relative_to((ROOT/'artifacts/api-multiagent-20261015').resolve())
    out.mkdir(parents=True,exist_ok=False)
    with httpx.Client(base_url='http://localhost:8001',headers={'Origin':'http://localhost:3000'},timeout=120) as client:
        credentials={'email':f'api-multiagent-{uuid4().hex[:10]}@example.com','password':secrets.token_urlsafe(24)}
        actor=checked(client.post('/api/v1/auth/register',json=credentials))
        provision(actor.get('id') or actor['user']['id'])
        client.headers['X-CSRF-Token']=checked(client.post('/api/v1/auth/login',json=credentials))['csrf_token']
        save(out/'qa-access.private.json',credentials)
        reports=[]
        for index,dataset in enumerate(args.dataset):
            case=dataset.resolve().parts[-3]
            subset=out/case
            subset.mkdir()
            source,receipt,gold,positive,external=fixture(dataset.resolve(),subset,args.entities,args.negative_controls)
            for variant_index,variant in enumerate(args.variants):
                ordinal = index*len(args.variants)+variant_index
                profile=checked(client.post('/api/v1/mdm/matching/profiles',json={
                    'version':ordinal+1,'integration':variant=='hybrid',
                    'advanced_agents':variant=='evidence','fallback_mode':'fail_operation'}))
                checked(client.post(f"/api/v1/mdm/matching/profiles/{profile['id']}/validate",json={'expected_revision':1}))
                checked(client.put('/api/v1/mdm/matching/defaults/owner-private',json={
                    'profile_id':profile['id'],'expected_revision':ordinal}))
                target=subset/variant
                target.mkdir()
                report=run_case(client,target,source,receipt,gold,positive,external,variant=variant,advice_limit=args.advice_limit,
                                model_scope=None if args.model_scope=='none' else args.model_scope,
                                explanation_samples=args.explanation_samples)
                report['profile_id']=profile['id']
                report['profile_digest']=profile['digest']
                save(target/'report.json',report)
                reports.append(report)
                save(out/'summary.json',{'status':'RUNNING','runs':reports})
        save(out/'summary.json',{'status':'PASS','runs':reports})
        print(json.dumps({'status':'PASS','runs':[r['run_id'] for r in reports]}),flush=True)


if __name__=='__main__':
    main()
