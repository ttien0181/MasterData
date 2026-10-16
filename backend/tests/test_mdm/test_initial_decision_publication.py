from sqlalchemy import event
from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowArtifact,WorkflowAssessment,WorkflowPair
from tests.test_mdm.test_workflow_stages import ready_decisions
import pytest


def test_initial_assessment_is_inserted_with_pairs_without_a_second_update_pass(client,workflow_run,monkeypatch):
    monkeypatch.setattr(get_settings(),'pair_assessment_auto_enabled',True)
    statements=[]
    engine=client.session_factory.kw['bind']
    def capture(conn,cursor,statement,parameters,context,executemany):
        statements.append(statement.lower())
    event.listen(engine,'before_cursor_execute',capture)
    try:
        state=ready_decisions(client,workflow_run)
    finally:
        event.remove(engine,'before_cursor_execute',capture)
    assert state['workflow_state']=='DECIDE_REVIEW'
    with client.session_factory() as db:
        from masterone.mdm.workflow_artifacts import load
        original=db.query(WorkflowArtifact).filter_by(run_id=workflow_run,phase='DECIDE').one()
        baseline={p['id']:p for p in load(db,original.id,workflow_run,'DECIDE')['pairs']}
        assessment=db.query(WorkflowAssessment).filter_by(run_id=workflow_run,active=True).one()
        assessed={p['id']:p for p in load(db,assessment.artifact_id,workflow_run,'PAIR_ASSESSMENT')['pairs']}
        rows=db.query(WorkflowPair).filter_by(run_id=workflow_run).all()
        assert len(rows)==len(baseline)==len(assessed)
        for row in rows:
            assert row.baseline==baseline[row.id]
            assert row.assessment==assessed[row.id]
            assert row.assessment_id==assessment.id
            assert row.decision=='UNDECIDED' and row.revision==0
    assert not any(s.startswith('update mdm_workflow_pairs ') for s in statements)


def test_hybrid_decide_seals_only_the_final_assessment(client,monkeypatch):
    from tests.test_mdm.test_matching_integration import hybrid_profile
    from tests.test_mdm.test_matching_foundation import new_run
    # Lifecycle test uses protocol-only vectors; this is not accuracy evidence.
    class Runtime:
        peak_rss=calls=retry_count=batch_attempts=0
        usage={}
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def encode(self,texts,field):return [[1.]+[0.]*383 for _ in texts]
    monkeypatch.setattr(get_settings(),'embedding_provider','e5_local')
    monkeypatch.setattr(get_settings(),'pair_assessment_auto_enabled',True)
    monkeypatch.setattr('masterone.mdm.matching_agents.workflow_agents.EncoderRuntime',Runtime)
    hybrid_profile(client,monkeypatch)
    run=new_run(client)
    state=ready_decisions(client,run)
    assert state['matching_agents']['status']=='OK'
    with client.session_factory() as db:
        assert db.query(WorkflowArtifact).filter_by(run_id=run,phase='PAIR_ASSESSMENT').count()==1


def test_failed_initial_insert_rolls_back_assessment_and_pair_publication(client,workflow_run,monkeypatch):
    from tests.test_mdm.test_workflow_stages import run_one,read
    from tests.test_mdm.test_workflow_contract import request
    monkeypatch.setattr(get_settings(),'pair_assessment_auto_enabled',True)
    run_one(client)
    state=read(client,workflow_run)
    response=client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/start-decide',json=request(state['workflow_revision']))
    assert response.status_code==202
    engine=client.session_factory.kw['bind']
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.lower().startswith('insert into mdm_workflow_pairs '):
            raise RuntimeError('TEST_PUBLICATION_ABORT')
    event.listen(engine,'before_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError,match='TEST_PUBLICATION_ABORT'):
            run_one(client)
    finally:
        event.remove(engine,'before_cursor_execute',fail)
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=workflow_run).count()==0
        assert db.query(WorkflowAssessment).filter_by(run_id=workflow_run,active=True).count()==0
