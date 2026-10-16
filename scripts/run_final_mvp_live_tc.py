"""HTTP + existing Docker QA worker, unchanged TC1 subset; no business mocks."""
import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/final-mvp-20261015/docker-live-tc-01'


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    assert args.attempt >= 1
    OUT = ROOT / f'artifacts/final-mvp-20261015/docker-live-tc-{args.attempt:02d}'
    OUT.mkdir(parents=True, exist_ok=False)
    labels = json.loads(subprocess.check_output(['docker', 'inspect', '--format', '{{json .Config.Labels}}',
                       'p119-session-qa-database-1'], text=True))
    assert labels['com.docker.compose.project'] == 'p119-session-qa'
    assert Path(labels['com.docker.compose.project.working_dir']).resolve() == ROOT.resolve()
    fixture_dir = ROOT / 'artifacts/final-mvp-20261015/one-tc-full-01'
    fixture = fixture_dir / 'qa-source-subset.csv'
    manifest = json.loads((fixture_dir / 'test-case-manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == manifest['fixture_sha256']
    started = time.perf_counter()
    steps, operations = [], []
    with httpx.Client(base_url='http://localhost:8001', headers={'Origin': 'http://localhost:3000'}, timeout=90) as client:
        def checked(response):
            assert response.status_code in (200, 201, 202), f'HTTP_{response.status_code}: {response.text[:400]}'
            return response.json()

        def timed(name, fn):
            print('START', name, flush=True)
            begin = time.perf_counter()
            value = fn()
            steps.append({'stage': name, 'seconds': time.perf_counter() - begin, 'status': 'PASS'})
            print('DONE', name, flush=True)
            return value

        credentials = {'email': f'final-live-{uuid4().hex[:10]}@example.com', 'password': 'qa-only-synthetic-passphrase'}
        registered = checked(client.post('/api/v1/auth/register', json=credentials))
        # Provision only the newly created QA fixture actor; never modify existing users or role mappings.
        identifier = registered.get('id') or registered.get('user', {}).get('id')
        assert identifier and re.fullmatch(r'[0-9a-f-]{36}', identifier)
        statement = f"UPDATE users SET role='STEWARD' WHERE id='{identifier}'; UPDATE llm_policy_control SET enabled=true,revision=revision+1 WHERE id='global';"
        subprocess.run(['docker', 'compose', 'exec', '-T', 'database', 'psql', '-U', 'masterone', '-d', 'masterone',
                        '-v', 'ON_ERROR_STOP=1', '-c', statement], check=True, capture_output=True)
        client.headers['X-CSRF-Token'] = checked(client.post('/api/v1/auth/login', json=credentials))['csrf_token']
        registry = '/api/v1/mdm/matching'
        profile = checked(client.post(registry + '/profiles', json={'version': 1}))
        checked(client.post(registry + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1}))
        checked(client.put(registry + '/defaults/owner-private', json={'profile_id': profile['id'], 'expected_revision': 0}))
        run = timed('upload-http', lambda: checked(client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                    files={'files': ('tc1-source-subset.csv', fixture.read_bytes(), 'text/csv')})))['run_id']
        base = f'/api/v1/mdm/runs/{run}'
        url = base + '/workflow'

        def state():
            return checked(client.get(url))

        def body(**extra):
            return {'request_id': str(uuid4()), 'expected_workflow_revision': state()['workflow_revision'], **extra}

        def wait_state(expected):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                current = state()
                assert current.get('latest_operation', {}).get('status') not in ('FAILED', 'RETRYABLE_FAILED'), current.get('latest_operation')
                if current['workflow_state'] == expected:
                    return current
                time.sleep(.25)
            raise TimeoutError('QA_WORKER_STATE_TIMEOUT_' + expected)

        def wait_operation(op):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                value = checked(client.get(url + '/operations/' + op['operation_id']))
                if value['status'] == 'COMPLETED':
                    operations.append({'operation_id': value['operation_id'], 'type': value['operation_type'], 'status': value['status']})
                    return value
                assert value['status'] not in ('FAILED', 'RETRYABLE_FAILED', 'DEGRADED'), value.get('error_code')
                time.sleep(.25)
            raise TimeoutError('QA_WORKER_OPERATION_TIMEOUT')

        try:
            timed('prepare-docker-worker', lambda: wait_state('DATA_READY'))
            operation = checked(client.post(url + '/start-decide', json=body()))
            timed('matching-docker-worker', lambda: wait_operation(operation))
            pairs = checked(client.get(url + '/pairs', params={'limit': 100}))['items']
            expected = {tuple(sorted(p)) for p in manifest['expected_positive_pairs']}
            negatives = {tuple(sorted(p)) for p in manifest['verified_negative_pairs']}
            record_keys = {}
            records = checked(client.get(url + '/data', params={'limit': 100}))
            for record in records['samples']['items']:
                record_keys[record['record_id']] = f"{record['source_system']}:{record['external_id']}:2026-Q1"
            assert len(record_keys) == manifest['rows']
            positive_pair = next(p for p in pairs if tuple(sorted((record_keys[p['baseline']['left_id']],
                record_keys[p['baseline']['right_id']]))) in expected)
            explanation = timed('live-gemini-through-docker-api', lambda: checked(client.post(url + f"/pairs/{positive_pair['pair_id']}/explanation", json=body())))
            assert explanation['status'] == 'READY', explanation.get('error_code')
            for pair in pairs:
                key = tuple(sorted((record_keys[pair['baseline']['left_id']], record_keys[pair['baseline']['right_id']])))
                decision = 'merge' if key in expected else 'deny' if key in negatives else 'no_merge'
                request = body(expected_pair_revision=pair['revision'], decision=decision)
                first = checked(client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request))
                assert checked(client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request)) == first
            members = sorted({p['baseline'][field] for p in pairs for field in ('left_id', 'right_id') if record_keys[p['baseline'][field]] in manifest['expected_positive_pairs'][0]})
            checked(client.post(url + '/group-approvals', json=body(expected_decision_revision=state()['decision_revision'],
                record_ids=members, reason='Scripted QA replay of synthetic source truth; not independent human gold')))
            operation = checked(client.post(url + '/finalize-decisions', json=body(expected_decision_revision=state()['decision_revision'])))
            timed('clustering-docker-worker', lambda: wait_operation(operation))
            clusters = checked(client.get(url + '/clusters'))['items']
            target = next(c for c in clusters if set(c['member_record_ids']) == set(members))
            assert sum(c['member_count'] for c in clusters) == manifest['rows']

            def versions():
                current = state()
                return {'expected_' + key: current[key] for key in ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')}

            def confirm_export():
                for cluster in checked(client.get(url + '/clusters'))['items']:
                    current = state()
                    checked(client.post(url + f"/clusters/{cluster['cluster_id']}/golden/confirm", json=body(
                        expected_cluster_revision=current['cluster_revision'], expected_golden_revision=current['golden_revision'])))
                checked(client.post(url + '/finalize-golden', json=body(expected_golden_revision=state()['golden_revision'])))
                preview = checked(client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False}))
                result = client.post(base + f"/previews/{preview['preview_id']}/execute", json={'execute_request_id': str(uuid4())})
                assert result.status_code == 200
                rows = list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig'))))
                assert sum(int(r['record_count']) for r in rows) == manifest['rows'] and len(rows) == 2
                assert all(r['master_record_id'] for r in rows)
                return result.content

            first_export = timed('golden-confirm-and-export-http', confirm_export)
            exports = checked(client.get(base + '/exports'))
            historical_id = (exports['items'] if isinstance(exports, dict) else exports)[0]['id']
            root = registry + f'/runs/{run}/structure'
            preview = checked(client.post(root + '/split/preview', json={'request_id': str(uuid4()), **versions(),
                'cluster_ids': [target['cluster_id']], 'partitions': [[members[0]], [members[1]]], 'reason': 'QA source-preserving split'}))
            assert preview['result']['blocked_reasons'] == []
            split = checked(client.post(root + '/execute', json={'request_id': str(uuid4()), **versions(),
                'preview_operation_id': preview['operation_id'], 'preview_token': preview['result']['preview_token']}))
            timed('split-docker-worker', lambda: wait_operation(split))
            pieces = [c for c in checked(client.get(url + '/clusters'))['items'] if set(c['member_record_ids']) <= set(members)]
            assert len(pieces) == 2
            preview = checked(client.post(root + '/remerge/preview', json={'request_id': str(uuid4()), **versions(),
                'cluster_ids': [c['cluster_id'] for c in pieces], 'constraint_id': split['result']['constraint_id'],
                'bridge_pair_ids': [positive_pair['pair_id']], 'reason': 'QA confirmation of one existing bridge; preserve Deny'}))
            assert preview['result']['blocked_reasons'] == []
            merge = checked(client.post(root + '/execute', json={'request_id': str(uuid4()), **versions(),
                'preview_operation_id': preview['operation_id'], 'preview_token': preview['result']['preview_token']}))
            timed('remerge-docker-worker', lambda: wait_operation(merge))
            final_export = timed('golden-reconfirm-and-export-http', confirm_export)
            assert client.get(base + f'/exports/{historical_id}/download').content == first_export
            feedback = checked(client.get(registry + f'/runs/{run}/feedback'))
            report = {'status': 'PASS', 'run_id': run, 'profile_id': profile['id'], 'profile_digest': profile['digest'],
                'operations': operations, 'steps': steps, 'seconds': time.perf_counter() - started,
                'dataset_manifest_sha256': hashlib.sha256((fixture_dir / 'test-case-manifest.json').read_bytes()).hexdigest(),
                'records': manifest['rows'], 'candidate_count': len(pairs), 'historical_export_unchanged': True,
                'final_export_sha256': hashlib.sha256(final_export).hexdigest(),
                'feedback_events': len(feedback), 'feedback_scope': 'SCRIPTED_SYNTHETIC_QA_NOT_INDEPENDENT_HUMAN_GOLD',
                'llm': {key: explanation.get(key) for key in ('status', 'provider', 'model_version', 'usage')},
                'embedding': 'DISABLED_IN_LINUX_QA_PINNED_RUNTIME_IS_WINDOWS_ONLY', 'production_deployment': False}
            (OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            (OUT / 'qa-export.csv').write_bytes(final_export)
            print(json.dumps({'status': 'PASS', 'run_id': run, 'seconds': report['seconds']}))
        except Exception as exc:
            (OUT / 'failure.json').write_text(json.dumps({'status': 'FAIL', 'run_id': run, 'steps': steps,
                'error_type': type(exc).__name__, 'error': str(exc)[:700]}, indent=2), encoding='utf-8')
            raise


if __name__ == '__main__':
    main()
