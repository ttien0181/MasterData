from copy import deepcopy
from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import ready_decisions, read, run_one


def accepted_fixture(client, run):
    ready_decisions(client, run)
    url=f'/api/v1/mdm/runs/{run}/workflow'
    with client.session_factory.begin() as db:
        pairs=db.query(WorkflowPair).filter_by(run_id=run).order_by(WorkflowPair.id).all()
        good=next(p for p in pairs if p.baseline['decision']=='MERGEABLE')
        original=deepcopy(good.baseline)
        # Keep an unresolved candidate on the same endpoints: it must not force
        # the user to review the entire region before publishing this Accept.
        db.add(WorkflowPair(id='pending-copy',run_id=run,baseline={**original,'id':'pending-copy','decision':'REVIEW','triage_status':'REVIEW'}))
        identifier=good.id
    state=read(client,run)
    result=client.post(url+f'/pairs/{identifier}/decision',json=request(state['workflow_revision'],expected_pair_revision=0,decision='merge'))
    assert result.status_code==200,result.text
    return url,identifier


def test_accept_queue_builds_while_pending_remains_and_repeat_is_empty(client,workflow_run):
    url,identifier=accepted_fixture(client,workflow_run)
    before=read(client,workflow_run)
    assert any(b['code']=='PENDING_DECISIONS' for b in before['blockers'])
    queue=client.get(url+'/cluster-queue').json()
    assert queue['pending_count']==1
    body=request(before['workflow_revision'],expected_decision_revision=before['decision_revision'],queue_mode='pending')
    response=client.post(url+'/cluster-queue',json=body)
    assert response.status_code==202,response.text
    assert client.post(url+'/cluster-queue',json=body).json()['operation_id']==response.json()['operation_id']
    run_one(client)
    after=read(client,workflow_run)
    assert after['cluster_revision']==1
    assert client.get(url+'/cluster-queue').json()['pending_count']==0
    pairs=client.get(url+'/pairs').json()['items']
    assert next(p for p in pairs if p['pair_id']=='pending-copy')['decision']=='UNDECIDED'
    assert next(p for p in pairs if p['pair_id']==identifier)['decision']=='ACCEPTED'
    clusters=client.get(url+'/clusters').json()
    assert any(c['member_count']==2 for c in clusters['items'])
    repeat=client.post(url+'/cluster-queue',json=request(after['workflow_revision'],expected_decision_revision=after['decision_revision'],queue_mode='pending'))
    assert repeat.status_code==409
    assert repeat.json()['detail']['code']=='CLUSTER_QUEUE_EMPTY'


def test_queue_command_rejects_stale_and_reading_does_not_enqueue(client,workflow_run):
    url,_=accepted_fixture(client,workflow_run)
    before=read(client,workflow_run)
    for _ in range(2):assert client.get(url+'/cluster-queue').status_code==200
    assert read(client,workflow_run)['active_operation'] is None
    response=client.post(url+'/cluster-queue',json=request(before['workflow_revision']-1,expected_decision_revision=before['decision_revision'],queue_mode='all'))
    assert response.status_code==409


def build(client,url,run,mode):
    state=read(client,run)
    response=client.post(url+'/cluster-queue',json=request(state['workflow_revision'],expected_decision_revision=state['decision_revision'],queue_mode=mode))
    assert response.status_code==202,response.text
    run_one(client)


def test_incremental_then_full_preserves_existing_group_choices_and_history(client,workflow_run):
    from masterone.mdm.workflow_models import WorkflowCluster,WorkflowGeneration
    url,_=accepted_fixture(client,workflow_run)
    build(client,url,workflow_run,'pending')
    with client.session_factory.begin() as db:
        generation=db.query(WorkflowGeneration).filter_by(run_id=workflow_run,revision=1).one()
        old=db.query(WorkflowCluster).filter_by(generation_id=generation.id).one()
        old.selections={'name':{'selection_mode':'MANUAL_OVERRIDE','value':'Chosen by Steward'}}
        old.confirmed=True
        pair=db.query(WorkflowPair).filter_by(run_id=workflow_run,id='pending-copy').one()
        identifier=pair.id
        old_id=old.id
    # ALL remains available with an empty queue and pending pairs still present.
    assert client.get(url+'/cluster-queue').json()['pending_count']==0
    build(client,url,workflow_run,'all')
    with client.session_factory() as db:
        assert db.query(WorkflowGeneration).filter_by(run_id=workflow_run).count()==2
        previous=db.get(WorkflowCluster,old_id)
        latest=db.query(WorkflowCluster).join(WorkflowGeneration).filter(WorkflowGeneration.run_id==workflow_run,WorkflowGeneration.revision==2).one()
        assert latest.payload['member_record_ids']==previous.payload['member_record_ids']
        assert latest.selections['name']['value']=='Chosen by Steward'
        assert previous.confirmed is True


def test_queue_rechecks_cannot_link_even_after_accept(client,workflow_run):
    from masterone.mdm.models import ResolutionNegativeEdge,ResolutionRun
    from masterone.mdm.workflow_cluster_queue import accepted_snapshot,compute_queue_publication
    from masterone.mdm.engine_adapter import engine_context
    url,identifier=accepted_fixture(client,workflow_run)
    with client.session_factory.begin() as db:
        pair=db.get(WorkflowPair,identifier)
        db.add(ResolutionNegativeEdge(run_id=workflow_run,left_record_id=pair.baseline['left_id'],right_record_id=pair.baseline['right_id'],created_by=db.get(ResolutionRun,workflow_run).owner_user_id,reason='STEWARD_REJECT',active=True))
    with client.session_factory() as db:
        snapshot=accepted_snapshot(db,db.get(ResolutionRun,workflow_run),'pending')
    with engine_context(snapshot['profile']):
        result=compute_queue_publication(snapshot,workflow_run,1)
    assert not any(len(c['member_record_ids'])>1 for c in result['clusters'])
    assert result['conflicts']


def test_second_queue_publication_retains_untouched_group_and_golden(client,workflow_run):
    from masterone.mdm.workflow_models import WorkflowCluster,WorkflowGeneration
    csv='id,source_system,name,phone,email,address\n'
    for i,(name,phone) in enumerate([('Nguyen Van An','0901234567'),('Tran Thi Binh','0912345678'),('Le Van Cuong','0923456789')]):
        for j in range(2):csv+=f'{i}-{j},{"CRM" if j==0 else "BILL"},{name},{phone},p{i}@example.test,123 Hanoi\n'
    uploaded=client.post('/api/v1/mdm/runs',data={'workflow_version':'2'},files={'files':('queue.csv',csv,'text/csv')})
    assert uploaded.status_code==201,uploaded.text
    run=uploaded.json()['run_id'];url=f'/api/v1/mdm/runs/{run}/workflow'
    # Finish the fixture's PREPARE so this independently uploaded run is next.
    run_one(client)
    ready_decisions(client,run)
    with client.session_factory() as db:
        candidates=db.query(WorkflowPair).filter_by(run_id=run).order_by(WorkflowPair.id).all()
        good=[p.id for p in candidates if p.baseline['decision']=='MERGEABLE']
    assert len(good)==3,[(p.baseline['decision'],p.baseline.get('reason')) for p in candidates]
    def accept(identifier):
        state=read(client,run)
        response=client.post(url+f'/pairs/{identifier}/decision',json=request(state['workflow_revision'],expected_pair_revision=0,decision='merge'))
        assert response.status_code==200,response.text
    accept(good[0]);build(client,url,run,'pending')
    with client.session_factory.begin() as db:
        generation=db.query(WorkflowGeneration).filter_by(run_id=run,revision=1).one()
        old=db.query(WorkflowCluster).filter_by(generation_id=generation.id).one()
        old.selections={'name':{'selection_mode':'MANUAL_OVERRIDE','value':'Keep this choice'}}
        members=set(old.payload['member_record_ids']);old_id=old.id
    accept(good[1])
    assert client.get(url+'/cluster-queue').json()['pending_count']==1
    build(client,url,run,'pending')
    with client.session_factory() as db:
        generation=db.query(WorkflowGeneration).filter_by(run_id=run,revision=2).one()
        groups=db.query(WorkflowCluster).filter_by(generation_id=generation.id).all()
        assert len(groups)==2
        preserved=next(g for g in groups if set(g.payload['member_record_ids'])==members)
        assert preserved.selections['name']['value']=='Keep this choice'
        assert db.get(WorkflowCluster,old_id) is not None
        assert len({r for group in groups for r in group.payload['member_record_ids']})==4
    assert client.get(url+'/cluster-queue').json()['pending_count']==0


def test_viewer_cannot_build_queue_or_publish_queued_job(client,workflow_run):
    from masterone.auth.models import User
    from masterone.mdm.models import ResolutionRun
    import pytest
    url,_=accepted_fixture(client,workflow_run)
    state=read(client,workflow_run)
    body=request(state['workflow_revision'],expected_decision_revision=state['decision_revision'],queue_mode='pending')
    response=client.post(url+'/cluster-queue',json=body)
    assert response.status_code==202,response.text
    with client.session_factory.begin() as db:
        db.get(User,db.get(ResolutionRun,workflow_run).owner_user_id).role='VIEWER'
    assert client.get(url+'/cluster-queue').status_code==200
    assert client.post(url+'/cluster-queue',json=body).status_code==403
    with pytest.raises(ValueError,match='WORKFLOW_AUTHORIZATION_CHANGED'):
        run_one(client)
    assert read(client,workflow_run)['cluster_revision']==0
    assert client.get(url+'/cluster-queue').json()['pending_count']==1


def test_all_with_no_accept_keeps_records_separate_without_reviewing_every_pair(client,workflow_run):
    ready_decisions(client,workflow_run)
    url=f'/api/v1/mdm/runs/{workflow_run}/workflow'
    build(client,url,workflow_run,'all')
    groups=client.get(url+'/clusters').json()
    assert groups['total']==3
    assert all(group['singleton'] for group in groups['items'])
    assert client.get(url+'/cluster-queue').json()['accepted_count']==0


def test_accept_bridge_extends_existing_group_without_duplicate_membership(client,workflow_run):
    from masterone.mdm.workflow_models import WorkflowCluster,WorkflowGeneration,WorkflowMember
    csv='source_record_id,source_system,name,phone,email,address,government_id,government_id_type,government_id_verified,government_id_namespace\n'+''.join(
        f'{i+1},{("CRM","BILL","ERP")[i]},Nguyen Van An,0901234567,a@example.test,123 Hanoi,001090123456,CCCD,true,VN\n' for i in range(3))
    response=client.post('/api/v1/mdm/runs',data={'workflow_version':'2'},files={'files':('bridge.csv',csv,'text/csv')})
    assert response.status_code==201,response.text
    run=response.json()['run_id'];url=f'/api/v1/mdm/runs/{run}/workflow'
    run_one(client);ready_decisions(client,run)
    with client.session_factory() as db:
        rows=db.query(WorkflowPair).filter_by(run_id=run).order_by(WorkflowPair.id).all()
        good=[p for p in rows if p.baseline['decision']=='MERGEABLE']
        assert len(good)==3,[(p.baseline['decision'],p.baseline.get('reason')) for p in rows]
        first,second=good[0].id,good[1].id
    for index,identifier in enumerate((first,second),1):
        state=read(client,run)
        accepted=client.post(url+f'/pairs/{identifier}/decision',json=request(state['workflow_revision'],expected_pair_revision=0,decision='merge'))
        assert accepted.status_code==200,accepted.text
        assert client.get(url+'/cluster-queue').json()['pending_count']==1
        build(client,url,run,'pending')
        with client.session_factory() as db:
            generation=db.query(WorkflowGeneration).filter_by(run_id=run,revision=index).one()
            groups=db.query(WorkflowCluster).filter_by(generation_id=generation.id).all()
            assert len(groups)==1
            assert len(groups[0].payload['member_record_ids'])==index+1
            assert db.query(WorkflowMember).filter_by(generation_id=generation.id).count()==index+1


def test_pending_scope_passes_snapshot_wide_contact_frequency_to_guards(client,workflow_run,monkeypatch):
    from masterone.mdm.models import ResolutionRun
    from masterone.mdm.workflow_cluster_queue import accepted_snapshot,compute_queue_publication
    from masterone.mdm import workflow_finalize
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.engine_adapter import engine_context
    accepted_fixture(client,workflow_run)
    with client.session_factory() as db:
        snapshot=accepted_snapshot(db,db.get(ResolutionRun,workflow_run),'pending')
    assert len(snapshot['queue_affected_records'])<len(snapshot['records'])
    observed=[];original=workflow_finalize.compute_publication
    def capture(*args,**kwargs):
        observed.append(kwargs.get('frequencies'))
        return original(*args,**kwargs)
    monkeypatch.setattr(workflow_finalize,'compute_publication',capture)
    with engine_context(snapshot['profile']):compute_queue_publication(snapshot,workflow_run,1)
    assert observed==[value_frequencies(snapshot['records'])]
