"""Synthetic HTTP smoke against the Compose API and independent QA worker."""
import json
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

os.environ['MASTERONE_ENV_FILE'] = '.env.qa'
os.environ['SESSION_WORKFLOW_ENABLED'] = 'true'

import httpx
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionEngineSelection, ResolutionJobAttempt
from masterone.mdm.workflow_models import (
    WorkflowArtifact,
    WorkflowGeneration,
    WorkflowMember,
    WorkflowOperation,
)
from sqlalchemy import select, text

OUT = Path('artifacts/session-workflow-2sessions/20261012/qa-smoke.json')
BASE = 'http://localhost:8001/api/v1'


def main():
    campaign = 'session-workflow-' + uuid4().hex
    email, password = campaign + '@example.com', secrets.token_urlsafe(32)
    settings = get_settings()
    engine, factory = create_database(settings.active_mdm_database_url)
    report = {'campaign': campaign, 'base_url': BASE, 'checks': [], 'runs': [],
              'llm_enabled': settings.llm_enabled, 'semantic_enabled': settings.semantic_address_enabled}
    origin = settings.cors_origins.split(',')[0].strip()
    client = httpx.Client(base_url=BASE, headers={'Origin': origin}, timeout=60)

    def call(method, path, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code != expected:
            detail = response.json().get('detail', {}) if 'json' in response.headers.get('content-type', '') else {}
            code = detail.get('code') if isinstance(detail, dict) else None
            raise RuntimeError(f'HTTP_{response.status_code}_{method}_{path.split("/")[-1]}_{code}')
        return response.json() if 'json' in response.headers.get('content-type', '') else response

    def body(state, **extra):
        return {'request_id': str(uuid4()), 'expected_workflow_revision': state['workflow_revision'], **extra}

    def wait(url, target):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = call('GET', url)
            if state['workflow_state'] == target:
                return state
            with factory() as db:
                failed = db.scalar(select(WorkflowOperation.error_code).where(WorkflowOperation.run_id == state['run_id'],
                    WorkflowOperation.status.in_(('RETRYABLE_FAILED', 'FAILED'))))
                if failed:
                    raise RuntimeError('WORKER_' + failed)
            time.sleep(1)
        raise TimeoutError('WORKFLOW_WAIT_' + target)

    try:
        call('POST', '/auth/register', expected=201, json={'email': email, 'password': password})
        with factory.begin() as db:
            owner = db.query(User).filter_by(email_normalized=email).one()
            owner.role = 'STEWARD'
            report['owner_id'], report['owner_email'] = owner.id, email
        login = call('POST', '/auth/login', json={'email': email, 'password': password})
        client.headers['X-CSRF-Token'] = login['csrf_token']
        # Synthetic only; one duplicate pair and one singleton. LLM settings stay as QA configured.
        content = (b'source_record_id,source_system,name,phone,email,address\n'
            b'1,CRM,Nguyen Van An,0901234567,a@example.test,123 Nguyen Trai Hanoi\n'
            b'2,BILL,Nguyen Van An,0901234567,a@example.test,123 Nguyen Trai Hanoi\n'
            b'3,CRM,Tran Thi Binh,0987654321,b@example.test,456 Le Loi HCM\n')
        created = call('POST', '/mdm/runs', expected=201, data={'workflow_version': '2'},
                       files={'files': (campaign + '.csv', content, 'text/csv')})
        run_id = created['run_id']
        report['runs'].append(run_id)
        url = '/mdm/runs/' + run_id + '/workflow'
        state = wait(url, 'DATA_READY')
        call('GET', url + '/data')
        with factory() as db:
            assert db.query(WorkflowOperation).filter_by(run_id=run_id).count() == 1
            assert db.query(WorkflowGeneration).filter_by(run_id=run_id).count() == 0
        report['checks'].append('upload_prepare_worker_no_cluster')
        call('POST', url + '/start-decide', expected=202, json=body(state))
        state = wait(url, 'DECIDE_REVIEW')
        pairs = call('GET', url + '/pairs')['items']
        for pair in pairs:
            if pair['baseline']['decision'] not in ('MERGEABLE', 'BLOCKED', 'NON_MATCH', 'NOT_MERGEABLE'):
                state = call('GET', url)
                call('POST', url + f"/pairs/{pair['pair_id']}/decision", json=body(state,
                    expected_pair_revision=pair['revision'], decision='no_merge'))
        state = call('GET', url)
        bulk = call('POST', url + '/bulk-decisions', expected=202, json=body(state,
            expected_decision_revision=state['decision_revision'], decision='merge', scope={'filter': {}}))
        deadline = time.monotonic() + 60
        while call('GET', url + '/operations/' + bulk['operation_id'])['status'] != 'COMPLETED':
            if time.monotonic() > deadline:
                raise TimeoutError('BULK_WAIT')
            time.sleep(1)
        for _ in range(3):
            call('GET', url)
            call('GET', url + '/pairs')
            call('GET', url + '/data')
        with factory() as db:
            assert db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='CLUSTER').count() == 0
            assert db.query(WorkflowGeneration).filter_by(run_id=run_id).count() == 0
        report['checks'].append('decide_review_bulk_get_no_cluster')
        state = call('GET', url)
        payload = body(state, expected_decision_revision=state['decision_revision'])
        # Competing tabs use distinct request IDs but the same revision.
        def competing(i):
            return client.post(url + '/finalize-decisions', json={**payload, 'request_id': f'{campaign}-finalize-{i}'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(competing, (1, 2)))
        assert sorted(r.status_code for r in responses) == [202, 409]
        winner = next(r for r in responses if r.status_code == 202).json()
        report['cluster_operation_id'] = winner['operation_id']
        state = wait(url, 'GOLDEN_REVIEW')
        clusters = call('GET', url + '/clusters')['items']
        assert sum(c['member_count'] for c in clusters) == 3
        assert any(c['singleton'] for c in clusters)
        report['checks'].append('postgres_finalize_race_one_cluster_worker_coverage')
        cluster = next(c for c in clusters if c['member_count'] > 1)
        cid = cluster['cluster_id']

        def golden_body(**extra):
            s = call('GET', url)
            return body(s, expected_cluster_revision=s['cluster_revision'], expected_golden_revision=s['golden_revision'], **extra)
        call('PUT', url + f'/clusters/{cid}/golden', json=golden_body(selections={
            'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'QA Server Choice'},
            'phone': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': cluster['member_record_ids'][0]}}))
        call('POST', url + '/golden/auto-select', json=golden_body(cluster_ids=[cid]))
        assert call('GET', url + f'/clusters/{cid}/golden')['fields']['name']['value'] == 'QA Server Choice'
        # Fresh login context reads the persisted choice.
        second = httpx.Client(base_url=BASE, headers={'Origin': origin}, timeout=60)
        assert second.post('/auth/login', json={'email': email, 'password': password}).status_code == 200
        assert second.get(url + f'/clusters/{cid}/golden').json()['fields']['name']['value'] == 'QA Server Choice'
        second.close()
        for c in clusters:
            call('POST', url + f"/clusters/{c['cluster_id']}/golden/confirm", json=golden_body())
        state = call('GET', url)
        final = call('POST', url + '/finalize-golden', json=body(state, expected_golden_revision=state['golden_revision']))
        preview = call('POST', f'/mdm/runs/{run_id}/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
        exported = call('POST', f'/mdm/runs/{run_id}/previews/{preview["preview_id"]}/execute',
            json={'execute_request_id': campaign})
        assert 'QA Server Choice' in exported.text
        report['export_id'] = exported.headers['X-Export-Id']
        downloaded = client.get(f'/mdm/runs/{run_id}/exports/{report["export_id"]}/download')
        if downloaded.status_code == 302:
            # A fresh client sends no API cookies/CSRF headers to object storage.
            downloaded = httpx.get(downloaded.headers['Location'], timeout=60)
        assert downloaded.status_code == 200
        assert downloaded.content == exported.content
        report['checks'].append('server_source_manual_auto_confirm_finalize_preview_export_reload_history')
        with factory() as db:
            assert db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='CLUSTER').count() == 1
            report['worker_attempts'] = [{'attempt_id': a.id, 'worker_id': a.worker_id, 'status': a.status}
                for a in db.query(ResolutionJobAttempt).filter_by(run_id=run_id)]
            report['profile'] = db.get(ResolutionEngineSelection, run_id).configuration
            report['operations'] = [{'operation_id': o.id, 'type': o.operation_type, 'status': o.status,
                'progress': o.progress, 'artifact_id': o.artifact_id} for o in
                db.query(WorkflowOperation).filter_by(run_id=run_id).order_by(WorkflowOperation.created_at)]
            report['artifacts'] = [{'artifact_id': a.id, 'phase': a.phase, 'hash': a.content_hash}
                for a in db.query(WorkflowArtifact).filter_by(run_id=run_id)]
            report['llm_stats'] = [a.payload.get('llm_stats') for a in db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='CLUSTER')]
            report['migration_revision'] = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
            report['counts'] = {'cluster_members': db.query(WorkflowMember).join(WorkflowGeneration).filter(WorkflowGeneration.run_id == run_id).count()}
        report['final'] = final
        report['status'] = 'PASS'
    except Exception as error:
        report['status'], report['error_type'] = 'FAIL', type(error).__name__
        if isinstance(error, (RuntimeError, TimeoutError)):
            report['error_code'] = str(error)  # Only codes explicitly constructed above.
    finally:
        client.close()
        engine.dispose()
        report['cleanup'] = 'retained_only_new_synthetic_owner_run_for_FE_replay'
        OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
