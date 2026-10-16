"""Exercise the real existing Docker API/worker on hash-verified TC1, without truth-based decisions."""
import argparse
import csv
import hashlib
import io
import json
import re
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from qa_dataset_adapter import FIELDS, decoded_rows

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/four-ai-20261015'
REGISTRY = '/api/v1/mdm/matching'


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def probe_address_component(run_id):
    """When natural selection is empty, test the real gateway with original TC1 addresses."""
    assert re.fullmatch(r'[0-9a-f-]{36}', run_id)
    code = '''
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from masterone.config import get_settings
from masterone.llm.gateway import generate
from masterone.llm.policy import PolicyEnforcer, PolicyDenied, allowed_payload, digest
from masterone.mdm.models import ResolutionRecord
run_id=RUN_ID
s=get_settings(); factory=sessionmaker(create_engine(s.active_mdm_database_url),expire_on_commit=False)
selected=[]; seen=set()
with factory() as db:
    for rid,fields in db.query(ResolutionRecord.id,ResolutionRecord.normalized_fields).filter_by(run_id=run_id).order_by(ResolutionRecord.id).yield_per(256):
        address=fields.get('address') or ''
        if address in seen: continue
        try: allowed_payload('ADDRESS_COMPARE',[{'pair_id':'probe','left_address':address,'right_address':address}])
        except PolicyDenied: continue
        selected.append((rid,address));seen.add(address)
        if len(selected)==2: break
assert len(selected)==2, 'TC1_ADDRESS_PROBE_INPUT_MISSING'
pair_id='probe-'+digest([r[0] for r in selected])[:24]
payload=[{'pair_id':pair_id,'left_address':selected[0][1],'right_address':selected[1][1]}]
context=PolicyEnforcer(s,factory).snapshot(run_id)
try:
    response=generate('ADDRESS_COMPARE',payload,s,context=context,factory=factory)
    print(json.dumps({'status':'SUCCESS','scope':'Controlled gateway component probe using two unchanged original TC1 addresses; not a naturally eligible annotation',
        'source_record_ids':[r[0] for r in selected], 'output':response['output'],
        'provider':response['provider'],'model_version':response['model_version'],
        'request_id':response['request_id'],'cached':response['cached'],'usage':response['usage']}))
except PolicyDenied as error:
    print(json.dumps({'status':'FAILED','error_code':error.code}))
'''.replace('RUN_ID', repr(run_id))
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'backend', 'python', '-'],
                            input=code, text=True, capture_output=True, check=True, encoding='utf8')
    probe = json.loads(result.stdout)
    write('address-component-probe.json', probe)
    assert probe['status'] == 'SUCCESS', 'ADDRESS_COMPONENT_PROBE_FAILED'
    return probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--new-attempt', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    verification = json.loads((OUT / 'dataset-verification.json').read_text(encoding='utf8'))
    assert verification['hashes_ok'] and verification['remote_manifest_matches_local']
    dataset = ROOT / 'artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7'
    catalog = yaml.safe_load((dataset / 'raw/source_mapping.yaml').read_text(encoding='utf8'))
    records = []
    for spec in catalog['files']:
        path = (dataset / spec['path']).resolve()
        assert path.is_relative_to((dataset / 'raw').resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == verification['source_hashes'][spec['path']]
        rows = iter(decoded_rows(path, spec))
        for _ in range(spec['header_row']):
            next(rows)
        header = next(rows)
        for values in rows:
            original = dict(zip(header, values, strict=True))
            records.append({field: original[spec['columns'][field]] for field in FIELDS})
    assert len(records) == verification['records']
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(records)
    source = OUT / 'source-only-input.csv'
    source.write_text(text.getvalue(), encoding='utf8')
    # Credentials stay in an ignored private env file, never in the public report.
    private = ROOT / '.env.four-ai-qa'
    if args.resume:
        credentials = json.loads(private.read_text(encoding='utf8'))
        report = json.loads((OUT / 'live-run.json').read_text(encoding='utf8'))
    elif args.new_attempt:
        credentials = json.loads(private.read_text(encoding='utf8'))
        previous = json.loads((OUT / 'live-run.json').read_text(encoding='utf8'))
        write('attempt-' + previous['run_id'] + '.json', previous)
        report = {'dataset': verification['dataset'], 'records': verification['records'],
                  'qa_owner_id': previous['qa_owner_id'],
                  'supplementary_run_ids': [*previous.get('supplementary_run_ids', []), previous['run_id']],
                  'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  'scope': 'Fresh immutable profile/run after a code fix; all earlier attempts retained in accounting',
                  'operations': [], 'explanations': []}
    else:
        assert not private.exists(), 'Use --resume to retain the existing QA owner/run'
        credentials = {'email': f'four-ai-{uuid4().hex[:12]}@example.com', 'password': secrets.token_urlsafe(32)}
        private.write_text(json.dumps(credentials), encoding='utf8')
        report = {'dataset': verification['dataset'], 'records': verification['records'],
                  'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  'scope': 'Real synthetic TC1 QA; no identity decisions or human labels are fabricated',
                  'operations': [], 'explanations': []}
    start = time.monotonic()

    def save():
        report['elapsed_seconds_this_invocation'] = round(time.monotonic() - start, 2)
        write('live-run.json', report)

    with httpx.Client(base_url='http://localhost:8001', headers={'Origin': 'http://localhost:3000'}, timeout=300) as client:
        def checked(response):
            if response.status_code not in (200, 201, 202):
                raise RuntimeError(f'HTTP_{response.status_code}: {response.text[:400]}')
            return response.json()

        if not args.resume and not args.new_attempt:
            user = checked(client.post('/api/v1/auth/register', json=credentials))
            identifier = user.get('id') or user.get('user', {}).get('id')
            assert identifier and re.fullmatch(r'[0-9a-f-]{36}', identifier)
            # Provision this newly created synthetic QA actor only, using the existing QA stack.
            sql = f"UPDATE users SET role='STEWARD' WHERE id='{identifier}';"
            subprocess.run(['docker', 'compose', 'exec', '-T', 'database', 'psql', '-U', 'masterone',
                            '-d', 'masterone', '-v', 'ON_ERROR_STOP=1', '-c', sql],
                           check=True, capture_output=True)
            report['qa_owner_id'] = identifier
            save()
        client.headers['X-CSRF-Token'] = checked(client.post('/api/v1/auth/login', json=credentials))['csrf_token']
        try:
            if not report.get('profile_id'):
                profiles = checked(client.get(REGISTRY + '/profiles'))
                pointer = checked(client.get(REGISTRY + '/defaults/owner-private'))
                profile = checked(client.post(REGISTRY + '/profiles', json={
                    'version': max((p['version'] for p in profiles), default=0) + 1,
                    'integration': True, 'fallback_mode': 'fail_operation'}))
                checked(client.post(REGISTRY + f"/profiles/{profile['id']}/validate", json={'expected_revision': 1}))
                checked(client.put(REGISTRY + '/defaults/owner-private', json={
                    'profile_id': profile['id'], 'expected_revision': pointer['revision']}))
                report['profile_id'], report['profile_digest'] = profile['id'], profile['digest']
                write('profile.json', profile)
                save()
            if not report.get('run_id'):
                with source.open('rb') as stream:
                    uploaded = checked(client.post('/api/v1/mdm/runs', data={
                        'workflow_version': '2', 'name': 'TC1 10K — four AI verification'},
                        files={'files': ('tc1-source-only.csv', stream, 'text/csv')}))
                report['run_id'] = uploaded['run_id']
                save()
            run = report['run_id']
            url = f'/api/v1/mdm/runs/{run}/workflow'

            def state():
                return checked(client.get(url))

            def body(**extra):
                current = state()
                return {'request_id': str(uuid4()), 'expected_workflow_revision': current['workflow_revision'],
                        'expected_assessment_id': current.get('assessment_id'), **extra}

            def wait_operation(operation):
                identifier = operation['operation_id']
                deadline, last = time.monotonic() + 2400, 0
                while time.monotonic() < deadline:
                    current = checked(client.get(url + '/operations/' + identifier))
                    if time.monotonic() - last > 30:
                        print(json.dumps({k: current.get(k) for k in
                              ['operation_type', 'status', 'stage', 'progress', 'error_code']}), flush=True)
                        last = time.monotonic()
                    if current['status'] == 'COMPLETED':
                        report['operations'] = [o for o in report['operations'] if o['operation_id'] != identifier]
                        report['operations'].append(current)
                        save()
                        return current
                    if current['status'] in ('FAILED', 'RETRYABLE_FAILED', 'CANCELLED', 'DEGRADED'):
                        report['failed_operation'] = current
                        save()
                        raise RuntimeError('OPERATION_' + str(current.get('error_code') or current['status']))
                    time.sleep(3)
                raise TimeoutError('OPERATION_TIMEOUT')

            current = state()
            if current['workflow_state'] == 'DATA_PREPARING':
                wait_operation(current['active_operation'])
                current = state()
            if current['workflow_state'] == 'DATA_READY':
                op = checked(client.post(url + '/start-decide', json=body()))
                report['decide_operation_id'] = op['operation_id']
                save()
                wait_operation(op)
            elif current['workflow_state'] == 'DECIDE_RUNNING':
                wait_operation(current['active_operation'])

            if not report.get('matching_agents'):
                agents = checked(client.get(REGISTRY + f'/runs/{run}/agents', params={'limit': 2}))
                report['matching_agents'] = {k: v for k, v in agents.items() if k != 'pairs'}
                assert agents['status'] == 'OK', 'AGENT_COVERAGE_NOT_COMPLETE'
                save()
            if not any(o['operation_type'] == 'PAIR_ADVICE' for o in report['operations']):
                advice = wait_operation(checked(client.post(url + '/pair-advice', json=body(limit=10))))
                assert advice['result']['assessed'] > 0, 'NO_REAL_PAIR_ADVICE'
            if not any(e['status'] == 'READY' for e in report['explanations']):
                if report['explanations']:
                    report.setdefault('previous_explanation_attempts', []).extend(report['explanations'])
                    report['explanations'] = []
                pairs = checked(client.get(url + '/pairs', params={'limit': 3}))['items']
                for pair in pairs:
                    result = checked(client.post(url + f"/pairs/{pair['pair_id']}/explanation", json=body()))
                    report['explanations'].append({'pair_id': pair['pair_id'], **result})
                    save()
            assert any(e['status'] == 'READY' for e in report['explanations']), 'NO_REAL_EXPLANATION'
            if not any(o['operation_type'] == 'CLUSTER' for o in report['operations']):
                current = state()
                # Explicit partial publication retains every undecided record; does not accept any pair.
                active, retryable = current.get('active_operation'), current.get('retryable_operation')
                if active and active['operation_type'] == 'CLUSTER':
                    wait_operation(active)
                elif retryable and retryable['operation_type'] == 'CLUSTER':
                    wait_operation(checked(client.post(
                        url + f"/operations/{retryable['operation_id']}/retry", json=body())))
                else:
                    wait_operation(checked(client.post(url + '/finalize-decisions', json=body(
                        expected_decision_revision=current['decision_revision'], allow_partial=True))))
            if not any(o['operation_type'] == 'GROUP_ANNOTATIONS' for o in report['operations']):
                current = state()
                active, retryable = current.get('active_operation'), current.get('retryable_operation')
                if active and active['operation_type'] == 'GROUP_ANNOTATIONS':
                    wait_operation(active)
                elif retryable and retryable['operation_type'] == 'GROUP_ANNOTATIONS':
                    wait_operation(checked(client.post(
                        url + f"/operations/{retryable['operation_id']}/retry", json=body())))
                else:
                    wait_operation(checked(client.post(url + '/group-annotations', json=body(
                        expected_cluster_revision=current['cluster_revision']))))
            annotation = next(o for o in report['operations'] if o['operation_type'] == 'GROUP_ANNOTATIONS')
            stats = annotation['result']['stats']
            if stats.get('selected') == 0 and stats.get('errors') == 0 and stats.get('stopped') is None:
                report['natural_address_annotation'] = 'NO_ELIGIBLE_TC1_PAIRS'
                report['address_component_probe'] = probe_address_component(run)
            else:
                assert stats.get('checked', 0) > 0, 'NO_REAL_ADDRESS_ANNOTATION'
                report['natural_address_annotation'] = 'REAL_ANNOTATIONS_VERIFIED'
            report['final_state'] = state()
            report['status'] = 'COMPLETED'
            report.pop('error', None)
            save()
            print(json.dumps({'status': report['status'], 'run_id': run}), flush=True)
        except Exception as error:
            report['status'], report['error'] = 'INCOMPLETE', str(error)[:500]
            save()
            raise


if __name__ == '__main__':
    main()
