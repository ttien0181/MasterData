"""Real workflow/worker checks; certificates here are isolated test fixtures only."""
import json
from hashlib import sha256
from uuid import uuid4
import pytest

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowOperation,WorkflowPair
from tests.test_mdm.test_workflow_stages import ready_decisions,read,run_one
from tests.test_mdm.test_pair_automation import certificate


def create(client,monkeypatch,csv_override=None):
    monkeypatch.setattr(get_settings(),'session_workflow_enabled',True)
    monkeypatch.setattr(get_settings(),'pair_assessment_auto_enabled',True)
    csv='source_record_id,source_system,entity_type,name,phone,email,dob,address\n1,CRM,PERSON,Nguyen Van An,0912345678,an.nguyen@example.net,1990-05-15,"123 Nguyen Trai, Ha Noi"\n2,ERP,PERSON,Nguyen Van An,0912345678,an.nguyen@example.net,1990-05-15,"123 Nguyen Trai, Ha Noi"\n'
    if csv_override:csv=csv_override
    response=client.post('/api/v1/mdm/runs',data={'workflow_version':'2'},files={'files':('auto.csv',csv.encode(),'text/csv')})
    assert response.status_code==201,response.text
    rid=response.json()['run_id'];ready_decisions(client,rid)
    return rid,f'/api/v1/mdm/runs/{rid}/workflow'


def body(client,rid,**extra):
    w=read(client,rid)
    return dict(request_id=str(uuid4()),expected_workflow_revision=w['workflow_revision'],
                expected_decision_revision=w['decision_revision'],expected_assessment_id=w['assessment_id'],**extra)


def shadow(client,rid,root):
    response=client.post(root+'/automation/shadow',json=body(client,rid))
    assert response.status_code==202,response.text
    run_one(client)
    return client.get(root+'/operations/'+response.json()['operation_id']).json()


def configure(client,monkeypatch,tmp_path,preview):
    from masterone.mdm.pipeline.pair_automation import code_identity
    from masterone.mdm.workflow_artifacts import load
    with client.session_factory() as db:
        p=load(db,preview['result']['decision_snapshot_id'],preview['run_id'],'DECISION_SNAPSHOT')
        from masterone.mdm.models import ResolutionRun
        owner=db.get(ResolutionRun,preview['run_id']).owner_user_id
    cohorts={c['cohort']:dict(correct=2000,labelled=2000,attempted=2000,constraint_violations=0) for c in preview['result']['components']}
    c=certificate(cohorts);c.update(code_identity=code_identity(),profile_identity=digest(p['profile']),
        source_systems=['CRM','ERP'],expires_at='2099-01-01T00:00:00Z',owner_user_id=owner,data_scope='isolated-fixture')
    raw=json.dumps(c).encode();path=tmp_path/'isolated-test-fixture.json';path.write_bytes(raw)
    for key,value in dict(pair_automation_enabled=True,pair_automation_certificate_path=str(path),
        pair_automation_certificate_sha256=sha256(raw).hexdigest(),pair_automation_max_components_per_run=1).items():
        monkeypatch.setattr(get_settings(),key,value)
    monkeypatch.setattr(get_settings(),'pair_automation_data_scope','isolated-fixture')
    return path


def apply(client,rid,root,preview):
    return client.post(root+'/automation/apply',json=body(client,rid,
        preview_operation_id=preview['operation_id'],preview_token=preview['result']['preview_token']))


def test_shadow_is_read_only_and_default_cannot_apply(client,monkeypatch):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    assert p['result']['candidate_components']==1 and not p['result']['can_apply']
    assert client.get(root+'/pairs').json()['items'][0]['decision']=='UNDECIDED'
    assert apply(client,rid,root,p).status_code==409


def test_certified_canary_replays_and_publication_keeps_automatic_provenance(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    assert p['result']['can_apply'] and p['result']['qualified_components']==1
    request=body(client,rid,preview_operation_id=p['operation_id'],preview_token=p['result']['preview_token'])
    r=client.post(root+'/automation/apply',json=request);assert r.status_code==202,r.text
    assert client.post(root+'/automation/apply',json=request).json()==r.json()
    run_one(client)
    pair=client.get(root+'/pairs').json()['items'][0]
    assert pair['decision']=='ACCEPTED' and pair['automation']['operation_id']==r.json()['operation_id']
    assert pair['automation']['certificate_hash']==get_settings().pair_automation_certificate_sha256
    assert pair['review_reason'] is None and pair['original_baseline']['decision']!='ACCEPTED'
    assert read(client,rid)['counts']['review_required']['value']==0
    final=client.post(root+'/finalize-decisions',json=body(client,rid));assert final.status_code==202,final.text
    run_one(client)
    assert client.get(root+'/clusters').json()['items'][0]['member_count']==2


def test_certificate_revocation_after_enqueue_leaves_all_decisions_unchanged(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    path=configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    assert apply(client,rid,root,p).status_code==202
    path.write_text('{}')
    with pytest.raises(ValueError,match='AUTOMATION'):
        run_one(client)
    assert client.get(root+'/pairs').json()['items'][0]['decision']=='UNDECIDED'


def test_stop_is_persistent_idempotent_and_fences_queued_application(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    assert apply(client,rid,root,p).status_code==202
    request={k:v for k,v in body(client,rid).items() if k!='expected_decision_revision'}
    stop=client.post(root+'/automation/stop',json=request);assert stop.status_code==200,stop.text
    assert client.post(root+'/automation/stop',json=request).json()==stop.json()
    from masterone.mdm.job_queue import claim_next
    assert claim_next(client.session_factory,'stopped-offline') is None
    assert client.get(root+'/pairs').json()['items'][0]['decision']=='UNDECIDED'


def test_forged_shadow_token_cannot_authorize_application(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    request=body(client,rid,preview_operation_id=p['operation_id'],preview_token='f'*64)
    response=client.post(root+'/automation/apply',json=request)
    assert response.status_code==409 and 'PREVIEW' in response.text


def test_manual_amendment_preserves_audit_but_does_not_refund_canary_budget(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    response=apply(client,rid,root,p);assert response.status_code==202,response.text
    run_one(client);pair=client.get(root+'/pairs').json()['items'][0]
    request=body(client,rid,expected_pair_revision=pair['revision'],decision='defer',reason='Need independent confirmation')
    request.pop('expected_decision_revision')
    amended=client.post(root+f"/pairs/{pair['pair_id']}/amend",json=request)
    assert amended.status_code==200,amended.text
    assert amended.json()['automation'] is None
    p=shadow(client,rid,root)
    assert p['result']['remaining_component_budget']==0 and not p['result']['can_apply']
    with client.session_factory() as db:
        assert db.get(WorkflowOperation,response.json()['operation_id']).result['applied_components']==1


def test_preview_is_bound_to_run_and_current_reviewer_decisions(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    other,other_root=create(client,monkeypatch)
    assert apply(client,other,other_root,p).status_code==409
    pair=client.get(root+'/pairs').json()['items'][0]
    request=body(client,rid,expected_pair_revision=pair['revision'],decision='no_merge')
    request.pop('expected_decision_revision')
    assert client.post(root+f"/pairs/{pair['pair_id']}/decision",json=request).status_code==200
    response=apply(client,rid,root,p)
    assert response.status_code==409 and 'PREVIEW_STALE' in response.text


def test_tampered_plan_summary_is_not_accepted_as_a_sealed_preview(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    with client.session_factory.begin() as db:
        op=db.get(WorkflowOperation,p['operation_id']);op.result={**op.result,'applicable_pairs':999}
    assert apply(client,rid,root,p).status_code==409


@pytest.mark.parametrize('changed',['code','input','authorization'])
def test_publish_rechecks_code_input_and_role_after_enqueue(client,monkeypatch,tmp_path,changed):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    assert apply(client,rid,root,p).status_code==202
    if changed=='code':
        monkeypatch.setattr('masterone.mdm.pipeline.pair_automation.code_identity',lambda:'x'*64)
    else:
        with client.session_factory.begin() as db:
            from masterone.mdm.models import ResolutionRecord,ResolutionRun
            if changed=='input':
                row=db.query(ResolutionRecord).filter_by(run_id=rid).first()
                row.normalized_fields={**row.normalized_fields,'dob':'1991-01-01'}
            else:
                from masterone.auth.models import User
                db.get(User,db.get(ResolutionRun,rid).owner_user_id).role='VIEWER'
    with pytest.raises(ValueError):run_one(client)
    with client.session_factory() as db:
        assert all(p.decision=='UNDECIDED' for p in db.query(WorkflowPair).filter_by(run_id=rid))


def test_stop_unlocks_manual_review_immediately_with_an_offline_worker(client,monkeypatch,tmp_path):
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    response=apply(client,rid,root,p);assert response.status_code==202
    request={k:v for k,v in body(client,rid).items() if k!='expected_decision_revision'}
    assert client.post(root+'/automation/stop',json=request).status_code==200
    w=read(client,rid)
    assert w['active_operation'] is None
    assert 'decide-pair' in w['allowed_actions']
    retry={k:v for k,v in body(client,rid).items() if k!='expected_decision_revision'}
    assert client.post(root+f"/operations/{response.json()['operation_id']}/retry",json=retry).status_code==409
    pair=client.get(root+'/pairs').json()['items'][0]
    request=body(client,rid,expected_pair_revision=pair['revision'],decision='no_merge');request.pop('expected_decision_revision')
    assert client.post(root+f"/pairs/{pair['pair_id']}/decision",json=request).status_code==200


def test_certified_components_after_uncertified_prefix_are_not_starved_by_cap(client,monkeypatch,tmp_path):
    csv='source_record_id,source_system,entity_type,name,phone,email,dob,address\n1,CRM,PERSON,Nguyen Van An,0912345678,an.nguyen@example.net,1990-05-15,"123 Nguyen Trai, Ha Noi"\n2,ERP,PERSON,Nguyen Van An,0912345678,an.nguyen@example.net,1990-05-15,"123 Nguyen Trai, Ha Noi"\n3,CRM,PERSON,Tran Thi Binh,0987654321,binh.tran@example.net,,"456 Nguyen Trai, Ha Noi"\n4,ERP,PERSON,Tran Thi Binh,0987654321,binh.tran@example.net,,"456 Nguyen Trai, Ha Noi"\n'
    rid,root=create(client,monkeypatch,csv);p=shadow(client,rid,root)
    assert p['result']['candidate_components']==2
    second=p['result']['components'][1]
    path=configure(client,monkeypatch,tmp_path,p);c=json.loads(path.read_bytes())
    c['cohorts']={second['cohort']:c['cohorts'][second['cohort']]}
    raw=json.dumps(c).encode();path.write_bytes(raw)
    monkeypatch.setattr(get_settings(),'pair_automation_certificate_sha256',sha256(raw).hexdigest())
    p=shadow(client,rid,root)
    assert p['result']['qualified_components']==1
    assert p['result']['qualified_fingerprints']==[second['fingerprint']]
    response=apply(client,rid,root,p);assert response.status_code==202,response.text
    run_one(client)
    accepted=[x for x in client.get(root+'/pairs').json()['items'] if x['decision']=='ACCEPTED']
    assert {p['pair_id'] for p in accepted}==set(second['pair_ids'])


def test_stop_retires_running_owner_before_manual_review_resumes(client,monkeypatch,tmp_path):
    from masterone.mdm.job_queue import claim_next,ownership,OwnershipLostError
    from masterone.mdm.workflow_jobs import execute_workflow
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    response=apply(client,rid,root,p);assert response.status_code==202
    claim=claim_next(client.session_factory,'running-stop',lease_seconds=300);assert claim
    request={k:v for k,v in body(client,rid).items() if k!='expected_decision_revision'}
    assert client.post(root+'/automation/stop',json=request).status_code==200
    assert read(client,rid)['active_operation'] is None
    with ownership(claim),pytest.raises(OwnershipLostError):
        execute_workflow(response.json()['operation_id'],client.session_factory,lambda *_:None)


def test_manual_repair_does_not_retain_automatic_decision_provenance(client,monkeypatch,tmp_path):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_artifacts import seal
    from masterone.mdm.workflow_finalize import decision_snapshot
    from masterone.mdm.workflow_resolution import versions
    rid,root=create(client,monkeypatch);p=shadow(client,rid,root)
    configure(client,monkeypatch,tmp_path,p);p=shadow(client,rid,root)
    response=apply(client,rid,root,p);assert response.status_code==202;run_one(client)
    pair=client.get(root+'/pairs').json()['items'][0]
    with client.session_factory() as db:
        run=db.get(ResolutionRun,rid);s=decision_snapshot(db,run);v=versions(run);owner=run.owner_user_id
    proposal={'status':'READY','changes':[{'pair_id':pair['pair_id'],'before':'ACCEPTED','revision':pair['revision']}],
        'before_record_ids':[pair['baseline']['left_id'],pair['baseline']['right_id']]}
    artifact=seal(client.session_factory,rid,'GOLDEN_REPAIR',{'proposal':proposal,'snapshot':s})
    with client.session_factory.begin() as db:
        op=WorkflowOperation(id=str(uuid4()),run_id=rid,request_id=str(uuid4()),operation_type='GOLDEN_RESOLVE',
            payload_hash=digest({}),payload={},status='COMPLETED',actor_user_id=owner,artifact_id=artifact,result={'versions':v})
        db.add(op);op_id=op.id
    request=body(client,rid,expected_cluster_revision=v['cluster_revision'],expected_golden_revision=v['golden_revision'],operation_id=op_id)
    response=client.post(root+'/golden/resolution/apply',json=request)
    assert response.status_code==202,response.text
    changed=client.get(root+'/pairs').json()['items'][0]
    assert changed['decision']=='REJECTED' and changed['automation'] is None
