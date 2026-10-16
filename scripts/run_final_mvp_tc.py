"""Real production workflow smoke on unchanged, hash-pinned TC1 source records.

Scripted QA decisions replay synthetic truth; they are NOT independent human gold.
No production DB, external models, runtime downloads or automatic merging.
"""
import argparse
import csv
import io
import json
import os
import sys
import time
from collections import defaultdict
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def digest_file(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--llm', action='store_true', help='One live Gemini explanation with QA policy, plus cache replay')
    parser.add_argument('--structure', action='store_true', help='Real S1 split/remerge and immutable historical export check')
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / 'artifacts/final-mvp-20261015').resolve()):
        parser.error('Output must be a new owned final-MVP QA directory')
    out.mkdir(parents=True, exist_ok=False)
    from run_merge_baseline import isolated_environment
    env = isolated_environment(out / 'state')
    env.update({'MASTERONE_ENV_FILE': str(out / 'absent.env'), 'SESSION_WORKFLOW_ENABLED': 'true',
                'PAIR_ASSESSMENT_AUTO_ENABLED': 'true', 'PAIR_AUTOMATION_ENABLED': 'false',
                'PAIR_REVIEW_MODEL_PATH': '', 'LLM_PAIR_ASSESSMENT_ENABLED': 'false',
                'MATCHING_PROFILE_REGISTRY_ENABLED': 'true', 'STEWARD_FEEDBACK_CAPTURE_ENABLED': 'false',
                'ADVANCED_MATCHING_AGENTS_ENABLED': 'true', 'ADVANCED_MATCHING_INTEGRATION_ENABLED': 'true',
                'WORKFLOW_PARQUET_ENABLED': 'false', 'MDM_SCORE_WORKERS': '1',
                'EMBEDDING_RUNTIME_ROOT': str(ROOT / 'artifacts/advanced-m2a-20261014/e5-trial-01'),
                'WORKFLOW_ARTIFACT_DIR': str(out / 'state/workflow-artifacts')})
    os.environ.update(env)
    os.environ['ENTITY_STRUCTURE_ENABLED'] = 'true' if args.structure else 'false'
    if args.llm:
        from dotenv import dotenv_values
        cloud = dotenv_values(ROOT / '.env.qa')
        for name in ('GEMINI_API_KEY', 'GEMINI_API_KEY_1', 'GEMINI_API_KEY_2', 'GEMINI_API_KEY_3',
                     'GEMINI_MODEL', 'LLM_APPROVED_PROVIDERS', 'LLM_APPROVED_MODELS',
                     'GEMINI_RETENTION_VERIFIED', 'GEMINI_RETENTION_CONTRACT', 'GEMINI_TOKEN_PRICE_MICROUSD'):
            if cloud.get(name):
                os.environ[name] = cloud[name]
        os.environ.update(LLM_ENABLED='true', LLM_EXPLANATION_ENABLED='true', GEMINI_MAX_RETRIES='0',
                          GEMINI_MAX_OUTPUT_TOKENS='512', LLM_GLOBAL_CALL_CAP='2', LLM_USER_CALL_CAP='2',
                          LLM_MAX_EXPLANATION_CALLS_PER_RUN='2', LLM_COST_CAP_MICROUSD='100000')
    sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'backend')]
    import pyarrow.parquet as pq
    import yaml
    from fastapi.testclient import TestClient
    from masterone.auth.models import User
    from masterone.config import get_settings
    from masterone.db.base import Base
    from masterone.db.session import get_session
    from masterone.main import app
    from masterone.mdm.job_queue import claim_next, heartbeat, ownership
    from masterone.mdm.job_queue import progress as job_progress
    from masterone.mdm.models import ResolutionAuditEvent, ResolutionRecord, ResolutionTask
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    dataset = ROOT / 'artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7'
    manifest = yaml.safe_load((dataset / 'manifest.yaml').read_text(encoding='utf-8'))
    hashes = {}
    for rel, expected in manifest['file_hashes'].items():
        path = (dataset / rel).resolve()
        assert path.is_relative_to(dataset.resolve()) and digest_file(path) == expected, 'SOURCE_HASH_MISMATCH'
        hashes[rel] = expected
    source_csv = ROOT / 'artifacts/advanced-m2b-20261014/experiments-01/E0/source-only-input.csv'
    rows = list(csv.DictReader(io.StringIO(source_csv.read_text(encoding='utf-8'))))
    by_key = {f"{r['source_system']}:{r['source_record_id']}:2026-Q1": r for r in rows}
    assert len(by_key) == len(rows) == 10022
    truth = pq.read_table(dataset / 'truth/records.parquet').to_pylist()
    groups = defaultdict(list)
    for row in truth:
        if row['entity_type'] == 'PERSON' and row['record_key'] in by_key:
            groups[row['entity_id']].append(row['record_key'])
    labels = pq.read_table(dataset / 'truth/answer_keys/evaluation_pairs.parquet').to_pylist()
    positive = {tuple(sorted((r['record_key_a'], r['record_key_b']))) for r in labels if r['label'] == 'MATCH'}
    negative = {tuple(sorted((r['record_key_a'], r['record_key_b']))) for r in labels if r['label'] == 'NON_MATCH'}
    choices = []
    for entity, keys in sorted(groups.items()):
        keys = sorted(keys)
        if len(keys) < 2 or not all(tuple(p) in positive for p in combinations(keys, 2)):
            continue
        if len({by_key[k]['source_system'] for k in keys}) != len(keys):
            continue
        controls = sorted(b if a in keys and b not in keys else a for a, b in negative
                          if (a in keys) != (b in keys) and a in by_key and b in by_key)
        if controls:
            diversity = len({by_key[k]['name'] for k in keys}) + len({by_key[k]['address'] for k in keys})
            anchors = sum(len({by_key[k][field].strip().casefold() for k in keys}) == 1
                          and bool(by_key[keys[0]][field].strip())
                          for field in ('government_id', 'dob', 'phone', 'email'))
            exact_name = len({by_key[k]['name'].strip().casefold() for k in keys}) == 1
            choices.append((-len(keys), -int(exact_name), -anchors, -diversity, entity, keys, controls[0]))
    assert choices, 'NO_LABELED_MULTI_RECORD_TC_WITH_NEGATIVE_CONTROL'
    _, _, _, _, entity, keys, control = sorted(choices)[0]
    chosen = keys + [control]
    payload = io.StringIO(newline='')
    writer = csv.DictWriter(payload, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(by_key[k] for k in chosen)
    fixture = out / 'qa-source-subset.csv'
    fixture.write_text(payload.getvalue(), encoding='utf-8')
    receipt = {'dataset': 'TC1_easy_match/10k/seed-7', 'truth_kind': 'SYNTHETIC_GENERATOR',
               'independent_human_gold': False, 'selected_entity_id': entity, 'source_keys': chosen,
               'source_hashes': hashes, 'source_only_anchor_sha256': digest_file(source_csv),
               'fixture_sha256': digest_file(fixture), 'rows': len(chosen),
               'sources': sorted({by_key[k]['source_system'] for k in chosen}),
               'expected_positive_pairs': [list(p) for p in combinations(keys, 2)],
               'verified_negative_pairs': [list(p) for p in sorted(negative) if set(p) <= set(chosen)],
               'limitations': ['CSV source is not BILL', 'TC1 PERSON entities have at most two source records',
                               'Scripted QA review is not independent human labeling']}
    (out / 'test-case-manifest.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    settings = get_settings()
    assert settings.active_mdm_database_url.startswith('sqlite:///') and not settings.r2_enabled
    assert settings.llm_enabled == args.llm
    engine = create_engine(settings.active_mdm_database_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    if args.llm:
        from masterone.mdm.models import LLMPolicyControl
        with factory.begin() as db:
            db.add(LLMPolicyControl(id='global', enabled=True, revision=1))

    def session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.state.session_factory = factory
    client = TestClient(app)
    client.headers['Origin'] = 'http://localhost:3000'
    steps, operations = [], []
    started = time.perf_counter()

    def checked(response):
        assert response.status_code in (200, 201, 202), f'API_{response.status_code}: {response.text[:700]}'
        return response.json()

    def timed(stage, fn):
        print('START', stage, flush=True)
        begin = time.perf_counter()
        try:
            value = fn()
        except Exception:
            steps.append({'stage': stage, 'status': 'FAIL', 'seconds': time.perf_counter() - begin})
            raise
        steps.append({'stage': stage, 'status': 'PASS', 'seconds': time.perf_counter() - begin})
        print('DONE', stage, flush=True)
        return value

    def worker():
        claim = claim_next(factory, 'final-mvp-owned-qa', lease_seconds=300)
        assert claim, 'EXPECTED_WORKER_TASK'
        with factory() as db:
            op_id = db.get(ResolutionTask, claim.task_id).operation_id
        last = [time.monotonic()]

        def report(phase, done, total):
            if time.monotonic() - last[0] > 25:
                heartbeat(factory, claim, 300)
                last[0] = time.monotonic()
            job_progress(factory, claim, phase, done, total)

        with ownership(claim):
            execute_workflow(op_id, factory, report)
        with factory() as db:
            op = db.get(WorkflowOperation, op_id)
            assert op.status == 'COMPLETED', op.status
            operations.append({'operation_id': op.id, 'type': op.operation_type, 'status': op.status})

    credentials = {'email': 'final-mvp-qa@example.com', 'password': 'qa-only-not-production-password'}
    checked(client.post('/api/v1/auth/register', json=credentials))
    with factory.begin() as db:
        db.query(User).filter_by(email_normalized=credentials['email']).one().role = 'STEWARD'
    client.headers['X-CSRF-Token'] = checked(client.post('/api/v1/auth/login', json=credentials))['csrf_token']
    registry = '/api/v1/mdm/matching'
    profile = checked(client.post(registry + '/profiles', json={'version': 1, 'integration': True}))
    checked(client.post(registry + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1}))
    checked(client.put(registry + '/defaults/owner-private', json={'profile_id': profile['id'], 'expected_revision': 0}))
    run = timed('upload-api', lambda: checked(client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                    files={'files': ('qa-source-subset.csv', fixture.read_bytes(), 'text/csv')})))['run_id']
    base = f'/api/v1/mdm/runs/{run}'
    url = base + '/workflow'

    def state():
        return checked(client.get(url))

    def request(**extra):
        return {'request_id': str(uuid4()), 'expected_workflow_revision': state()['workflow_revision'], **extra}

    try:
        timed('prepare-production-worker', worker)
        with factory() as db:
            records = db.query(ResolutionRecord).filter_by(run_id=run).all()
            assert len(records) == len(chosen)
            id_keys = {r.id: f'{r.source_system}:{r.external_id}:2026-Q1' for r in records}
            standardization = [{'record_id': r.id, 'source_key': id_keys[r.id],
                                'quality_flags': r.quality_flags, 'normalized_field_names': sorted(r.normalized_fields)} for r in records]
        checked(client.post(url + '/start-decide', json=request()))
        timed('candidate-rule-embedding-coordinator-worker', worker)
        pairs = checked(client.get(url + '/pairs', params={'limit': 100}))['items']
        endpoints = {tuple(sorted((id_keys[p['baseline']['left_id']], id_keys[p['baseline']['right_id']]))) for p in pairs}
        expected = {tuple(p) for p in combinations(keys, 2)}
        assert expected <= endpoints, 'EXPECTED_CANDIDATES_MISSING'
        assert len(endpoints) == len(pairs) and all(a != b for a, b in endpoints)
        evidence = checked(client.get(registry + f'/runs/{run}/agents'))
        assert evidence['status'] == 'OK' and evidence['total_pairs'] == len(pairs)
        llm_result = {'status': 'NOT_REQUESTED'}
        if args.llm:
            target_pair = next(p for p in pairs if tuple(sorted((id_keys[p['baseline']['left_id']],
                               id_keys[p['baseline']['right_id']]))) in expected)
            endpoint = url + f"/pairs/{target_pair['pair_id']}/explanation"
            first = timed('live-gemini-explanation-api', lambda: checked(client.post(endpoint, json=request())))
            llm_result = {key: first.get(key) for key in ('status', 'provider', 'model_version', 'usage', 'error_code')}
            if first['status'] == 'READY':
                replay_result = checked(client.post(endpoint, json=request()))
                assert replay_result['status'] == 'READY' and replay_result['usage']['dispatch_attempts'] == 0
                llm_result['cache_replay_dispatch_attempts'] = 0

        def review():
            for pair in pairs:
                key = tuple(sorted((id_keys[pair['baseline']['left_id']], id_keys[pair['baseline']['right_id']])))
                decision = 'merge' if key in expected else 'deny' if key in negative else 'no_merge'
                body = request(expected_pair_revision=pair['revision'], decision=decision)
                response = checked(client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body))
                assert checked(client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body)) == response
        timed('scripted-qa-review-synthetic-truth-not-human-gold', review)
        timed('explicit-qa-group-review-under-existing-guards', lambda: checked(client.post(url + '/group-approvals',
            json=request(expected_decision_revision=state()['decision_revision'],
                         record_ids=sorted(rid for rid, key in id_keys.items() if key in keys),
                         reason='QA replay of hash-pinned synthetic entity truth; no independent human gold claim'))))
        current = state()
        checked(client.post(url + '/finalize-decisions', json=request(expected_decision_revision=current['decision_revision'])))
        timed('cluster-publication-worker', worker)
        clusters = checked(client.get(url + '/clusters'))['items']
        memberships = [rid for c in clusters for rid in c['member_record_ids']]
        assert len(memberships) == len(set(memberships)) == len(chosen)
        target = next(c for c in clusters if c['member_count'] == len(keys))
        assert {id_keys[rid] for rid in target['member_record_ids']} == set(keys)

        def confirm():
            for cluster in clusters:
                current = state()
                checked(client.post(url + f"/clusters/{cluster['cluster_id']}/golden/confirm", json=request(
                    expected_cluster_revision=current['cluster_revision'], expected_golden_revision=current['golden_revision'])))
            checked(client.post(url + '/finalize-golden', json=request(expected_golden_revision=state()['golden_revision'])))
        timed('golden-confirm-api', confirm)

        def export():
            preview = checked(client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False}))
            response = client.post(base + f"/previews/{preview['preview_id']}/execute", json={'execute_request_id': str(uuid4())})
            assert response.status_code == 200, response.text[:500]
            exported = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
            assert sum(int(r['record_count']) for r in exported) == len(chosen) and len(exported) == 2
            assert all(r.get('master_record_id') for r in exported)
            (out / 'qa-export.csv').write_bytes(response.content)
            return {'rows': len(exported), 'record_count': len(chosen), 'sha256': sha256(response.content).hexdigest(),
                    'columns': list(exported[0])}
        exported = timed('export-production-api', export)
        structure_result = {'status': 'NOT_TESTED'}
        if args.structure:
            export_rows = checked(client.get(base + '/exports'))
            exports_list = export_rows['items'] if isinstance(export_rows, dict) else export_rows
            historical_id = exports_list[0]['id']
            original_bytes = client.get(base + f'/exports/{historical_id}/download').content

            def versions():
                current = state()
                return {'expected_' + key: current[key] for key in
                        ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')}

            structure_url = registry + f'/runs/{run}/structure'
            body = {'request_id': str(uuid4()), **versions(), 'cluster_ids': [target['cluster_id']],
                    'partitions': [[target['member_record_ids'][0]], target['member_record_ids'][1:]],
                    'reason': 'Scripted QA structure smoke on unchanged TC1 records; not identity labels'}
            split_preview = timed('split-preview-api', lambda: checked(client.post(structure_url + '/split/preview', json=body)))
            assert split_preview['result']['blocked_reasons'] == []
            split = checked(client.post(structure_url + '/execute', json={'request_id': str(uuid4()), **versions(),
                            'preview_operation_id': split_preview['operation_id'], 'preview_token': split_preview['result']['preview_token']}))
            assert split['status'] == 'QUEUED'
            timed('split-publication-worker', worker)
            groups = checked(client.get(url + '/clusters'))['items']
            pieces = [c for c in groups if set(c['member_record_ids']) <= set(target['member_record_ids'])]
            assert len(pieces) == 2
            for piece in pieces:
                golden = checked(client.get(url + f"/clusters/{piece['cluster_id']}/golden"))
                assert golden['status'] == 'DRAFT', 'CHANGED_MEMBERSHIP_MUST_RECONFIRM_GOLDEN'
            bridge = next(p['pair_id'] for p in pairs if tuple(sorted((id_keys[p['baseline']['left_id']],
                          id_keys[p['baseline']['right_id']]))) in expected)
            body = {'request_id': str(uuid4()), **versions(), 'cluster_ids': [c['cluster_id'] for c in pieces],
                    'bridge_pair_ids': [bridge], 'constraint_id': split['result']['constraint_id'],
                    'reason': 'Explicitly confirm this existing QA bridge, preserve independent Deny'}
            remerge_preview = timed('remerge-preview-api', lambda: checked(client.post(structure_url + '/remerge/preview', json=body)))
            assert remerge_preview['result']['blocked_reasons'] == []
            checked(client.post(structure_url + '/execute', json={'request_id': str(uuid4()), **versions(),
                'preview_operation_id': remerge_preview['operation_id'], 'preview_token': remerge_preview['result']['preview_token']}))
            timed('remerge-publication-worker', worker)
            clusters = checked(client.get(url + '/clusters'))['items']
            assert any({id_keys[r] for r in c['member_record_ids']} == set(keys) for c in clusters)
            timed('golden-reconfirm-after-remerge-api', confirm)
            exported = timed('export-after-remerge-api', export)
            assert client.get(base + f'/exports/{historical_id}/download').content == original_bytes
            structure_result = {'status': 'PASS', 'split_preview_operation_id': split_preview['operation_id'],
                'remerge_preview_operation_id': remerge_preview['operation_id'], 'constraint_id': split['result']['constraint_id'],
                'historical_export_sha256': sha256(original_bytes).hexdigest(), 'historical_export_unchanged': True}
        with factory() as db:
            assert db.query(ResolutionRecord).filter_by(run_id=run).count() == len(chosen)
            audits = [{'action': e.action, 'id': e.id} for e in db.query(ResolutionAuditEvent).filter_by(run_id=run)]
            overlays = {p.id: {'decision': p.decision, 'revision': p.revision} for p in db.query(WorkflowPair).filter_by(run_id=run)}
        report = {'status': 'PASS', 'run_id': run, 'profile_id': profile['id'], 'profile_digest': profile['digest'],
                  'operations': operations, 'steps': steps, 'seconds': time.perf_counter() - started,
                  'candidate_count': len(pairs), 'clusters': [{'cluster_id': c['cluster_id'], 'entity_id': c.get('entity_id'),
                     'member_count': c['member_count']} for c in clusters], 'standardization': standardization,
                  'review_overlays': overlays, 'audit_events': audits, 'export': exported,
                  'feedback': 'DISABLED_FOR_SCRIPTED_QA_REVIEW_TO_AVOID_AUTOMATED_HUMAN_LABELS',
                  'llm': llm_result,
                  'split_remerge': structure_result, 'deployment': 'NOT_PERFORMED',
                  'truth_scope': receipt['limitations'], 'ml_quality_claim': False}
        (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'status': 'PASS', 'run_id': run, 'candidate_count': len(pairs), 'seconds': report['seconds']}))
    except Exception as exc:
        (out / 'failure.json').write_text(json.dumps({'status': 'FAIL', 'run_id': run, 'steps': steps,
            'operations': operations, 'error_type': type(exc).__name__, 'error': str(exc)[:1000]}, indent=2), encoding='utf-8')
        raise
    finally:
        files = {str(p.relative_to(out)): digest_file(p) for p in out.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        (out / 'manifest.json').write_text(json.dumps(files, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
