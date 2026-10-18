import csv
import hashlib
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from masterone.auth.models import DemoGrant, User
from masterone.config import get_settings
from masterone.main import app
from masterone.mdm.demo_dataset import SOURCES, csv_bytes, zip_bytes
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from tests.test_mdm.test_workflow_stages import run_one, read


def enable(monkeypatch):
    settings = get_settings()
    for name in ('demo_enabled', 'session_workflow_enabled', 'matching_profile_registry_enabled',
                 'advanced_matching_agents_enabled'):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, 'embedding_provider', 'openrouter')


def enter(client):
    response = client.post('/api/v1/auth/demo')
    assert response.status_code == 201, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return response.json()


def test_sample_files_are_reproducible_valid_synthetic_csv():
    hashes = []
    for i in range(4):
        rows = list(csv.DictReader(io.StringIO(csv_bytes(i).decode('utf-8-sig'))))
        assert len(rows) == 500
        assert len({row['source_record_id'] for row in rows}) == 500
        assert {row['source_system'] for row in rows} == {SOURCES[i][1]}
        assert all(not row['email'] or row['email'].endswith('@example.test') for row in rows)
        hashes.append(hashlib.sha256(csv_bytes(i)).hexdigest())
    assert len(set(hashes)) == 4
    with zipfile.ZipFile(io.BytesIO(zip_bytes())) as archive:
        assert set(archive.namelist()) == {s[0] for s in SOURCES} | {'HUONG_DAN.txt'}


def test_demo_is_scoped_blocks_all_uploads_and_preserves_normal_login(client, monkeypatch):
    enable(monkeypatch)
    user = enter(client)
    assert user['role'] == 'STEWARD'
    assert 'export_data' in user['capabilities'] and 'explain' in user['capabilities']
    assert 'create_run' not in user['capabilities'] and 'learn_match_profile' not in user['capabilities']
    assert client.get('/api/v1/auth/me').json()['demo']['state'] == 'NEW'
    for path in ('/api/v1/mdm/upload-sessions', '/api/v1/mdm/runs'):
        result = client.post(path, json={})
        assert result.status_code == 403, result.text
    # Scope rejects uploads even if the UI is bypassed with valid multipart data.
    assert client.post('/api/v1/mdm/runs', data={'workflow_version':'2'},
        files={'files':('records.csv',csv_bytes(0),'text/csv')}).status_code == 403
    result = client.post('/api/v1/mdm/demo/start')
    assert result.status_code == 200, result.text
    run_id = result.json()['run_id']
    assert client.post('/api/v1/mdm/demo/start').json()['run_id'] == run_id
    with client.session_factory() as db:
        assert db.query(ResolutionRun).filter_by(owner_user_id=user['id']).count() == 1
        assert db.query(ResolutionRecord).filter_by(run_id=run_id).count() == 2000
    other = TestClient(app)
    other.headers.update({'Origin':'http://localhost:3000'})
    enter(other)
    assert other.get(f'/api/v1/mdm/runs/{run_id}/workflow').status_code == 404
    assert other.get('/api/v1/mdm/runs').json()['total'] == 0
    assert client.post('/api/v1/auth/logout').status_code == 200
    assert client.get('/api/v1/auth/me').json()['email'] == 'steward@example.com'


def test_demo_prepare_automatically_queues_real_embedding_without_r2(client, monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_artifact_dir', str(tmp_path/'artifacts'))
    def forbidden(*args, **kwargs): raise AssertionError('demo must not access R2')
    monkeypatch.setattr('masterone.storage.r2._cached_store', forbidden)
    for name in ('r2_endpoint_url', 'r2_access_key_id', 'r2_secret_access_key', 'r2_bucket'):
        monkeypatch.setattr(get_settings(), name, 'configured-but-forbidden')
    enter(client)
    result = client.post('/api/v1/mdm/demo/start', json={'use_embedding': True})
    assert result.status_code == 200, result.text
    run_id = result.json()['run_id']
    run_one(client)
    state = read(client, run_id)
    assert state['workflow_state'] == 'DECIDE_RUNNING'
    assert state['active_operation']['operation_type'] == 'DECIDE'
    from masterone.mdm.workflow_models import WorkflowOperation
    with client.session_factory() as db:
        op = db.get(WorkflowOperation, state['active_operation']['operation_id'])
        assert op.payload['mvp_step2'] is True
        assert op.payload['demo_fast_mode'] is False
        assert op.payload['matching_binding_id']


def test_demo_expiry_and_disabled_entry_are_enforced_server_side(client, monkeypatch):
    assert client.post('/api/v1/auth/demo').status_code == 404
    enable(monkeypatch)
    user = enter(client)
    with client.session_factory.begin() as db:
        db.get(DemoGrant, user['id']).last_seen_epoch = int(time.time()) - 7201
    assert client.get('/api/v1/auth/me').status_code == 401
    from masterone.mdm.demo_service import expire_demos
    expire_demos(client.session_factory)
    with client.session_factory() as db:
        assert db.get(DemoGrant, user['id']).state == 'EXPIRED'
        assert not db.get(User, user['id']).is_active


def test_catalog_downloads_are_authenticated_and_whitelisted(client):
    assert client.get('/api/v1/mdm/demo/catalog').json()['record_count'] == 2000
    response = client.get('/api/v1/mdm/demo/files/masterone-du-lieu-mau.zip')
    assert response.status_code == 200 and response.content[:2] == b'PK'
    assert client.get('/api/v1/mdm/demo/files/unknown.csv').status_code == 404
    client.cookies.clear()
    assert client.get('/api/v1/mdm/demo/files/crm_khach_hang.csv').status_code == 401


def test_expired_demo_data_is_purged_without_touching_other_runs(client, workflow_run, monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_artifact_dir', str(tmp_path))
    user = enter(client)
    run_id = client.post('/api/v1/mdm/demo/start').json()['run_id']
    with client.session_factory.begin() as db:
        from masterone.mdm.demo_service import end_demo
        grant = db.get(DemoGrant,user['id'])
        end_demo(db,grant)
        grant.expires_at_epoch = int(time.time())-400
    from masterone.mdm.demo_cleanup import purge
    purge(client.session_factory)
    with client.session_factory() as db:
        assert db.get(DemoGrant,user['id']).state == 'PURGED'
        assert db.get(ResolutionRun,run_id) is None
        assert db.query(ResolutionRecord).filter_by(run_id=run_id).count() == 0
        assert db.get(ResolutionRun,workflow_run) is not None


def test_demo_embedding_cache_reuses_only_real_vectors_with_new_scope(client, monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_artifact_dir', str(tmp_path))
    from masterone.mdm.demo_embeddings import context,identities,warm,seed
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowArtifact,WorkflowOperation
    from masterone.mdm.generation_staging import digest
    def prepared(run):
        run_one(client)
        with client.session_factory() as db:
            op=db.query(WorkflowOperation).filter_by(run_id=run,operation_type='DECIDE').one()
            row=db.query(WorkflowArtifact).filter_by(run_id=run,phase='PREPARE').one()
            records=load(db,row.id,run,'PREPARE')['records']
            return op.id,records
    first=enter(client)
    run_id=client.post('/api/v1/mdm/demo/start', json={'use_embedding': True}).json()['run_id']
    op_id,records=prepared(run_id)
    with client.session_factory.begin() as db:
        op,grant,binding,config=context(db,op_id)
        keys=[key for *_,key in identities(records[:2],(grant.user_id,run_id),config)]
        vector=[1.0]+[0.0]*(config['encoder_identity']['dimension']-1)
        value={'input_hash':digest(records),'profile_digest':binding.profile_digest,'vectors':{k:vector for k in keys}}
        db.add(WorkflowArtifact(run_id=run_id,phase='AGENT_VECTOR_CACHE',payload=value,content_hash=digest(value)))
        op.status='COMPLETED'
        from masterone.mdm.models import ResolutionTask
        db.query(ResolutionTask).filter_by(run_id=run_id).one().status='COMPLETED'
    warm(client.session_factory,op_id)
    second=enter(client)
    next_run=client.post('/api/v1/mdm/demo/start', json={'use_embedding': True}).json()['run_id']
    next_op,next_records=prepared(next_run)
    assert first['id']!=second['id']
    assert seed(client.session_factory,next_op,next_records)>0
    with client.session_factory() as db:
        row=db.query(WorkflowArtifact).filter_by(run_id=next_run,phase='AGENT_VECTOR_CACHE').one()
        cache=load(db,row.id,next_run,'AGENT_VECTOR_CACHE')
        assert not set(cache['vectors']) & set(keys)
        assert cache['origin']=='REAL_DEMO_EMBEDDING_CACHE'


def test_normal_new_steward_upload_also_queues_comparison_without_clicking_process(client, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(),'workflow_auto_process',True)
    response=client.post('/api/v1/mdm/runs', data={'workflow_version':'2'},
        files=[('files',(name,csv_bytes(i),'text/csv')) for i,(name,_,_) in enumerate(SOURCES)])
    assert response.status_code==201,response.text
    run_id=response.json()['run_id']
    run_one(client)
    state=read(client,run_id)
    assert state['workflow_state']=='DECIDE_RUNNING'
    assert state['active_operation']['operation_type']=='DECIDE'
    assert client.get('/api/v1/auth/me').json()['demo'] is None


def test_demo_defaults_to_rule_only_and_never_runs_embedding(client, monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', False)
    monkeypatch.setattr(get_settings(), 'workflow_artifact_dir', str(tmp_path))
    def forbidden(*args, **kwargs):
        raise AssertionError('Embedding must not run when disabled')
    monkeypatch.setattr('masterone.mdm.matching_agents.workflow_agents.run_agents', forbidden)
    monkeypatch.setattr('masterone.mdm.demo_embeddings.seed', forbidden)
    enter(client)
    response = client.post('/api/v1/mdm/demo/start')
    assert response.status_code == 200, response.text
    run_id = response.json()['run_id']
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_models import WorkflowPair
    with client.session_factory() as db:
        assert db.get(ResolutionRun, run_id).use_embedding is False
    run_one(client)
    assert read(client, run_id)['workflow_state'] == 'DECIDE_RUNNING'
    run_one(client)
    state = read(client, run_id)
    assert state['active_operation'] is None, state
    with client.session_factory() as db:
        pairs = db.query(WorkflowPair).filter_by(run_id=run_id).all()
        assert pairs
        assert all(pair.assessment.get('embedding_enabled') is False for pair in pairs)
    # Rechecking the same session must preserve the choice, too.
    from tests.test_mdm.test_workflow_contract import request
    response = client.post(f'/api/v1/mdm/runs/{run_id}/workflow/reassess',
        json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        assert all(pair.assessment.get('embedding_enabled') is False
                   for pair in db.query(WorkflowPair).filter_by(run_id=run_id))


def test_demo_can_opt_into_embedding_and_resume_keeps_original_choice(client, monkeypatch):
    enable(monkeypatch)
    enter(client)
    response = client.post('/api/v1/mdm/demo/start', json={'use_embedding': True})
    assert response.status_code == 200, response.text
    run_id = response.json()['run_id']
    assert client.post('/api/v1/mdm/demo/start', json={'use_embedding': False}).json()['run_id'] == run_id
    with client.session_factory() as db:
        assert db.get(ResolutionRun, run_id).use_embedding is True


def test_upload_pins_rule_only_choice_and_rejects_mode_change_on_retry(client, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_auto_process', True)
    monkeypatch.setattr(get_settings(), 'advanced_matching_agents_enabled', False)
    from tests.test_mdm.test_upload_sessions import _create_draft, _upload_payload
    from masterone.mdm.models import ResolutionUploadSession
    payload = (b'record_id,source,name,phone,email,address\n'
               b'ONE,CRM,Ada,0901234567,ada@example.test,Ha Noi\n'
               b'TWO,ERP,Ada,0901234567,ada@example.test,Ha Noi\n')
    draft = _create_draft(client, 'people.csv', payload)
    _upload_payload(client, draft, payload)
    url = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    assert client.post(url+'/validate').json()['status'] == 'READY'
    result = client.post(url+'/commit?workflow_version=2&use_embedding=false')
    assert result.status_code == 201, result.text
    run_id = result.json()['run_id']
    assert client.get(url).json()['use_embedding'] is False
    with client.session_factory() as db:
        assert db.get(ResolutionRun, run_id).use_embedding is False
        assert db.get(ResolutionUploadSession, draft['session_id']).use_embedding is False
    assert client.post(url+'/commit?workflow_version=2&use_embedding=true').status_code == 409
    assert client.post(url+'/commit?workflow_version=2&use_embedding=false').json()['run_id'] == run_id
    run_one(client)
    assert read(client, run_id)['workflow_state'] == 'DECIDE_RUNNING'


def test_demo_rejects_invalid_embedding_choices(client, monkeypatch):
    enable(monkeypatch)
    enter(client)
    assert client.post('/api/v1/mdm/demo/start', json={'use_embedding':'false'}).status_code == 422
    assert client.post('/api/v1/mdm/demo/start', json={'use_embedding':False, 'files':['external.csv']}).status_code == 422


def test_async_upload_worker_reads_persisted_embedding_choice(client, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_auto_process', True)
    from tests.test_mdm.test_upload_sessions import _create_draft, _upload_payload
    from masterone.mdm.models import ResolutionUploadSession
    from masterone.mdm.upload_sessions import commit_upload_session
    payload = b'record_id,source,name,phone\nONE,CRM,Ada,0901234567\n'
    draft = _create_draft(client, 'people.csv', payload)
    _upload_payload(client, draft, payload)
    url = f"/api/v1/mdm/upload-sessions/{draft['session_id']}"
    assert client.post(url+'/validate').json()['status'] == 'READY'
    monkeypatch.setattr(get_settings(), 'import_jobs_inline', False)
    def queue_only(*args, before_start=None, **kwargs):
        before_start()
    monkeypatch.setattr('masterone.mdm.import_jobs.start', queue_only)
    result = client.post(url+'/commit?workflow_version=2&use_embedding=false')
    assert result.status_code == 202, result.text
    # Simulate the separate import process reading committed state on a new connection.
    with client.session_factory() as db:
        session = db.get(ResolutionUploadSession, draft['session_id'])
        assert session.status == 'COMMITTING' and session.use_embedding is False
        run, _ = commit_upload_session(db, session, session.owner_user_id, begun=True)
        assert run.use_embedding is False


def test_disabling_embedding_for_one_upload_preserves_owner_default(client, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(get_settings(), 'workflow_auto_process', True)
    from masterone.mdm.matching_foundation_models import MatchingBinding, MatchingProfile, MatchingProfileDefault
    def upload(enabled):
        response = client.post('/api/v1/mdm/runs', data={'workflow_version':'2', 'use_embedding':str(enabled).lower()},
            files={'files':('records.csv', csv_bytes(0), 'text/csv')})
        assert response.status_code == 201, response.text
        return response.json()['run_id']
    first = upload(True)
    with client.session_factory() as db:
        owner = db.get(ResolutionRun, first).owner_user_id
        original_default = db.get(MatchingProfileDefault, (owner, 'owner-private')).profile_id
    second = upload(False)
    with client.session_factory() as db:
        assert db.get(MatchingProfileDefault, (owner, 'owner-private')).profile_id == original_default
        binding = db.query(MatchingBinding).filter_by(run_id=second).one()
        assert db.get(MatchingProfile, binding.profile_id).config_json['kind'] == 'RULE_ONLY'
        assert db.get(MatchingProfile, original_default).config_json['kind'] == 'RULE_WITH_EMBEDDING_EVIDENCE'
