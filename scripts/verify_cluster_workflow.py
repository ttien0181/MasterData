"""One smallest original TC through isolated API and actual leased workers.

declared-fixtures is simulated review, not a human approval or production quality
certificate. Truth is opened only after the final predictions have been captured.
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fixture_choice(pair):
    if (pair['eligibility']['manual_allowed'] and not pair['eligibility'].get('reason_required')
            and pair['baseline']['decision'] == 'MERGEABLE' and pair['baseline'].get('primary_group') == 'A'):
        return 'merge'
    return 'no_merge'


def quality(owners, candidates, groups):
    actual = defaultdict(list)
    for rid, eid in owners.items():
        if eid is not None:
            actual[eid].append(rid)
    positive = {tuple(sorted(pair)) for members in actual.values() for pair in combinations(members, 2)}
    predicted = {tuple(sorted(pair)) for members in groups for pair in combinations(members, 2)}
    candidate = {tuple(sorted(pair)) for pair in candidates}
    group_sets = {frozenset(group) for group in groups}
    tp = len(predicted & positive)
    return {'positive_pairs': len(positive), 'predicted_pairs': len(predicted), 'pair_tp': tp,
        'pair_fp': len(predicted-positive), 'pair_precision': tp/len(predicted) if predicted else None,
        'pair_recall': tp/len(positive) if positive else None,
        'candidate_recall': len(candidate & positive)/len(positive) if positive else None,
        'junk_co_cluster_pairs': sum(owners[a] is None or owners[b] is None for a,b in predicted),
        'exact_entity_matches': sum(frozenset(m) in group_sets for m in actual.values())}


def completion_status(*, actual, expected, finalized, exported, integrity):
    if not integrity or actual != expected:
        return 'INCOMPLETE_INPUT'
    if not finalized:
        return 'REVIEW_PENDING'
    return 'SIMULATED_REVIEW_END_TO_END' if exported else 'INCOMPLETE_EXPORT'


def run(bundle, output, review_mode, parquet):
    import yaml
    from fetch_cluster_bundles import checksum, target_path, verify_existing
    from qa_dataset_adapter import adapt
    from run_merge_baseline import isolated_environment
    output.mkdir(parents=True, exist_ok=False)
    manifest = yaml.safe_load((bundle / 'manifest.yaml').read_text(encoding='utf-8'))
    pinned = {name: checksum(target_path(bundle, name)) for name in manifest['file_hashes']}
    for name, expected in manifest['file_hashes'].items():
        verify_existing(target_path(bundle, name), expected)
    source_files = sorted((ROOT / 'backend/src/masterone').rglob('*.py'))
    code_hashes = {str(p.relative_to(ROOT)): checksum(p) for p in source_files}
    inventory = adapt(bundle, output / 'adapted')
    os.environ.update(isolated_environment(output / 'state'))
    os.environ['WORKFLOW_ARTIFACT_DIR'] = str((output / 'state/workflow-artifacts').resolve())
    sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'backend')]
    from masterone.config import get_settings
    from masterone.mdm.export_stream import rss_peak
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.models import ResolutionRecord, ResolutionRun, ResolutionTask
    from masterone.mdm.workflow_jobs import execute_workflow
    from pytest import MonkeyPatch
    from sqlalchemy import event
    from tests.test_mdm.conftest import client as client_fixture
    patch = MonkeyPatch()
    patch.setattr(get_settings(), 'session_workflow_enabled', True)
    patch.setattr(get_settings(), 'workflow_parquet_enabled', parquet)
    patch.setattr(get_settings(), 'pair_assessment_auto_enabled', True)
    fixture = client_fixture.__wrapped__(patch)
    client = next(fixture)
    statements, stages, review_actions = [0], [], Counter()
    def statement(*_):
        statements[0] += 1
    event.listen(client.session_factory.kw['bind'], 'before_cursor_execute', statement)
    def stage(name, command):
        start, count = time.perf_counter(), statements[0]
        print('START ' + name, flush=True)
        result = command()
        stages.append({'stage': name, 'wall_seconds': time.perf_counter()-start,
                       'db_statements': statements[0]-count, 'rss_process_high_water_bytes': rss_peak()})
        print('DONE ' + name + ' ' + str(round(stages[-1]['wall_seconds'], 3)), flush=True)
        return result
    def checked(response, expected=(200, 202)):
        if response.status_code not in expected:
            raise RuntimeError(str(response.status_code) + ' ' + response.text[:1000])
        return response.json()
    def worker():
        claim = claim_next(client.session_factory, 'small-bundle-qa', lease_seconds=900, timeout_seconds=900)
        if claim is None:
            raise ValueError('QA_EXPECTED_OWNED_JOB')
        with client.session_factory() as db:
            oid = db.get(ResolutionTask, claim.task_id).operation_id
        with ownership(claim):
            execute_workflow(oid, client.session_factory, lambda *_: None)
    try:
        files = [('files', (p.name, p.read_bytes(), 'text/csv')) for p in sorted((output / 'adapted').glob('*.csv'))]
        uploaded = stage('import-api', lambda: checked(client.post('/api/v1/mdm/runs', data={'workflow_version':'2'}, files=files), (201,)))
        rid = uploaded['run_id']
        root = f'/api/v1/mdm/runs/{rid}/workflow'
        def state():
            return checked(client.get(root))
        serial = [0]
        def mutation(**fields):
            serial[0] += 1
            w = state()
            return {'request_id': 'small-qa-' + str(serial[0]), 'expected_workflow_revision': w['workflow_revision'],
                    'expected_assessment_id': w.get('assessment_id'), **fields}
        stage('prepare-worker', worker)
        checked(client.post(root + '/start-decide', json=mutation()))
        stage('decide-assessment-worker', worker)
        candidates, decisions = [], {'merge': [], 'no_merge': []}
        cursor = None
        while True:
            page = checked(client.get(root + '/pairs', params={'limit':100, **({'cursor':cursor} if cursor else {})}))
            for pair in page['items']:
                candidates.append((pair['baseline']['left_id'], pair['baseline']['right_id']))
                if pair['decision'] == 'UNDECIDED':
                    decisions[fixture_choice(pair)].append(pair['pair_id'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        if review_mode == 'declared-fixtures':
            def review():
                for decision, ids in decisions.items():
                    for offset in range(0, len(ids), 1000):
                        w = state()
                        body = mutation(expected_decision_revision=w['decision_revision'], decision=decision,
                                        scope={'pair_ids':ids[offset:offset+1000]}, auto_only=False)
                        preview = checked(client.post(root + '/bulk-decisions/preview', json=body))
                        body['preview_token'] = preview['preview_token']
                        checked(client.post(root + '/bulk-decisions', json=body))
                        worker()
                        review_actions[decision] += len(ids[offset:offset+1000])
            stage('simulated-source-only-review', review)
        w = state()
        checked(client.post(root + '/cluster-preview', json=mutation(expected_decision_revision=w['decision_revision'], allow_partial=True)))
        stage('preview-worker', worker)
        w = state()
        checked(client.post(root + '/finalize-decisions', json=mutation(expected_decision_revision=w['decision_revision'], allow_partial=True)))
        stage('publication-worker', worker)
        w = state()
        def group_pages():
            groups, cursor = [], None
            while True:
                page = checked(client.get(root + '/clusters', params={'limit':100, **({'cursor':cursor} if cursor else {})}))
                groups.extend(item['member_record_ids'] for item in page['items'])
                cursor = page['next_cursor']
                if cursor is None:
                    return groups
        groups = stage('group-pages-api', group_pages)
        finalized, exported, csv_rows, csv_ids = False, False, 0, []
        if w['workflow_state'] == 'GOLDEN_REVIEW':
            stage('confirm-golden-api', lambda: checked(client.post(root + '/golden/confirm-selected', json=mutation(
                expected_cluster_revision=w['cluster_revision'], expected_golden_revision=w['golden_revision']))))
            w = state()
            response = stage('finalize-golden-api', lambda: client.post(root + '/finalize-golden', json=mutation(
                expected_golden_revision=w['golden_revision'])))
            checked(response)
            if response.status_code == 202:
                stage('finalization-worker', worker)
            finalized = state()['workflow_state'] == 'FINALIZED'
            base = f'/api/v1/mdm/runs/{rid}'
            preview = checked(client.post(base + '/previews', json={'choices':{}, 'acknowledge_unreviewed':False}))
            def export():
                response = client.post(base + '/previews/' + preview['preview_id'] + '/execute', json={'execute_request_id':'small-bundle-export'})
                if response.status_code != 200:
                    raise RuntimeError(response.text[:1000])
                (output / 'export.csv').write_bytes(response.content)
            stage('export-api', export)
            with (output / 'export.csv').open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream):
                    csv_rows += 1
                    csv_ids.extend(row['source_record_ids'].split(';'))
            exported = True
        # Predictions are frozen before opening external ownership labels.
        import pyarrow.parquet as pq
        truth = pq.read_table(bundle / 'truth/records.parquet').to_pylist()
        truth_by_key = {row['record_key']:row['entity_id'] for row in truth}
        with client.session_factory() as db:
            record_keys = {r.id:r.extra_fields['qa_record_key'] for r in db.query(ResolutionRecord).filter_by(run_id=rid).yield_per(512)}
            actual = db.get(ResolutionRun, rid).record_count
        owners = {record_id:truth_by_key[key] for record_id,key in record_keys.items()}
        quality_result = quality(owners, candidates, groups)
        if exported and (len(csv_ids) != actual or len(set(csv_ids)) != actual or set(csv_ids) != set(record_keys)):
            raise ValueError('QA_EXPORT_COVERAGE_INVALID')
        for name, checksum_before in pinned.items():
            verify_existing(target_path(bundle, name), checksum_before)
        for relative, checksum_before in code_hashes.items():
            verify_existing(ROOT / relative, checksum_before)
        report = {'case':bundle.parent.parent.name, 'scale':bundle.parent.name, 'review_mode':review_mode,
            'review_actions':dict(review_actions), 'truth_used_to_review':False, 'parquet_enabled':parquet,
            'status':completion_status(actual=actual, expected=manifest['actual_records'], finalized=finalized, exported=exported, integrity=True),
            'input_records':actual, 'adaptation':inventory, 'quality':quality_result, 'workflow_state':state()['workflow_state'],
            'coverage':state()['cluster_result'], 'finalized':finalized, 'exported':exported, 'csv_rows':csv_rows,
            'csv_member_count':len(csv_ids), 'stages':stages, 'code_hashes':code_hashes, 'input_hashes':pinned,
            'limitations':['Synthetic PRIOR/L0 source', 'Simulated review is not human approval',
                'No 100k/1M certification', 'RSS is process high-water, not individual stage peak',
                'Browser verification is a separate check'], 'adaptation_inventory_hash': checksum(output / 'adapted/inventory.json')}
        (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('status','input_records','quality','workflow_state','exported')}, indent=2), flush=True)
        return report
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
        patch.undo()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--review-mode', choices=['none','declared-fixtures'], default='none')
    parser.add_argument('--parquet', action='store_true')
    args = parser.parse_args()
    if args.bundle.parent.name != '10k':
        parser.error('Only one smallest 10k original bundle is authorized')
    run(args.bundle.resolve(), args.output.resolve(), args.review_mode, args.parquet)


if __name__ == '__main__':
    main()
