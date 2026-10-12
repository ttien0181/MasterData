"""Owned synthetic browser fixture and read-only QA DB assertions; secrets stay in pipes."""
import json
import csv
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

os.environ['MASTERONE_ENV_FILE'] = '.env.qa'
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionEngineSelection, ResolutionExport, ResolutionRun
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowGeneration, WorkflowMember, WorkflowOperation
import httpx


def main():
    command = json.load(sys.stdin)
    settings = get_settings()
    engine, factory = create_database(settings.active_mdm_database_url)
    try:
        if command['action'] == 'seed':
            email = 'browser-workflow-' + uuid4().hex + '@example.com'
            password = secrets.token_urlsafe(32)
            response = httpx.post('http://localhost:8001/api/v1/auth/register',
                headers={'Origin': 'http://localhost:13000'}, json={'email': email, 'password': password}, timeout=60)
            assert response.status_code == 201, 'REGISTER_FAILED'
            with factory.begin() as db:
                owner = db.query(User).filter_by(email_normalized=email).one()
                owner.role = 'STEWARD'
            print(json.dumps({'email': email, 'password': password}))
            return
        if command['action'] == 'legacy':
            with httpx.Client(base_url='http://localhost:8001/api/v1', headers={'Origin': 'http://localhost:13000'}, timeout=60) as client:
                response = client.post('/auth/login', json={'email': command['email'], 'password': command['password']})
                assert response.status_code == 200, 'LEGACY_LOGIN_FAILED'
                client.headers['X-CSRF-Token'] = response.json()['csrf_token']
                response = client.post('/mdm/runs', files={'files': ('legacy-browser.csv', b'source_record_id,source_system,name,phone\n1,CRM,Legacy Synthetic,0901234567\n', 'text/csv')})
                assert response.status_code == 201, 'LEGACY_CREATE_FAILED'
                print(json.dumps({'run_id': response.json()['run_id']}))
            return
        with factory() as db:
            run = db.get(ResolutionRun, command['run_id'])
            owner = db.query(User).filter_by(email_normalized=command['email']).one()
            assert run.owner_user_id == owner.id, 'FIXTURE_OWNER_MISMATCH'
            generations = db.query(WorkflowGeneration).filter_by(run_id=run.id).all()
            selection = db.get(ResolutionEngineSelection, run.id)
            report = {
                'run_id': run.id, 'workflow_version': run.workflow_version, 'state': run.workflow_state, 'records': run.record_count,
                'workflow_revision': run.workflow_revision, 'cluster_revision': run.cluster_revision,
                'golden_revision': run.golden_revision,
                'cluster_operations': db.query(WorkflowOperation).filter_by(run_id=run.id, operation_type='CLUSTER').count(),
                'generations': len(generations),
                'memberships': db.query(WorkflowMember).filter(WorkflowMember.generation_id.in_([g.id for g in generations])).count(),
                'clusters': db.query(WorkflowCluster).filter(WorkflowCluster.generation_id.in_([g.id for g in generations])).count(),
                'artifacts': [{'phase': a.phase, 'hash': a.content_hash} for a in db.query(WorkflowArtifact).filter_by(run_id=run.id)],
                'exports': db.query(ResolutionExport).filter_by(run_id=run.id).count(),
                'engine_profile': selection.configuration if selection else None,
            }
            if command['action'] == 'csv':
                generation = next(g for g in generations if g.revision == run.cluster_revision)
                result = generation.finalized_result
                with Path('artifacts/session-workflow-2sessions/20261012-fe/browser-export.csv').open(encoding='utf-8-sig', newline='') as stream:
                    rows = list(csv.DictReader(stream))
                from masterone.mdm.workflow_exports import field_value
                assert len(rows) == len(result['clusters'])
                by_id = {row['master_record_id']: row for row in rows}
                covered = []
                for cluster in result['clusters']:
                    row = by_id[cluster['id']]
                    covered.extend(row['source_record_ids'].split(';'))
                    assert int(row['record_count']) == len(cluster['member_record_ids'])
                    for field, column in {'name': 'full_name', 'phone': 'phone_number', 'email': 'email_address', 'address': 'raw_address'}.items():
                        assert row[column] == str(field_value(field, cluster['golden_fields'].get('fields', {}).get(field, {})))
                assert len(covered) == len(set(covered)) == run.record_count
                assert set(covered) == set(result['records'])
                report.update(csv_equal_server=True, result_hash=generation.result_hash, covered_records=len(covered))
            print(json.dumps(report))
    finally:
        engine.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('QA_FIXTURE_FAILED_' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
