"""Demo examples use real source data and publication, never human training labels."""
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowOperation, WorkflowPair
from masterone.mdm.matching_foundation_models import StewardFeedback
from tests.test_mdm.test_product_demo import enable, enter
from tests.test_mdm.test_workflow_stages import read, run_one


def test_demo_publishes_editable_groups_and_export_without_judge_decisions(client, monkeypatch):
    enable(monkeypatch)
    enter(client)
    run_id = client.post('/api/v1/mdm/demo/start').json()['run_id']
    assert client.post('/api/v1/mdm/demo/examples').json()['status'] == 'WAITING'
    run_one(client)
    run_one(client)
    assert read(client, run_id)['active_operation']['operation_type'] == 'CLUSTER'
    run_one(client)
    state = read(client, run_id)
    assert state['cluster_revision'] == 1, state
    assert state['golden_revision'] > 0
    assert state['active_operation'] is None
    base = f'/api/v1/mdm/runs/{run_id}/workflow'
    page = client.get(base + '/golden/index?limit=5').json()
    assert len(page['items']) == 5 and page['next_cursor']
    from tests.test_mdm.test_workflow_golden import golden_request
    from tests.test_mdm.test_golden_selection_export import rows
    cid = page['items'][0]['cluster_id']
    assert len(rows(client.post(base + '/golden/export', json=golden_request(client, run_id, cluster_ids=[cid])))) == 1
    with client.session_factory() as db:
        groups = db.query(WorkflowCluster).all()
        assert len(groups) == 8
        assert all(len(group.payload['member_record_ids']) == 4 for group in groups)
        assert db.query(StewardFeedback).filter_by(run_id=run_id).count() == 0
        assert db.query(WorkflowPair).filter_by(run_id=run_id, decision='UNDECIDED').count() > 0
        revisions = (state['workflow_revision'], state['decision_revision'])
    for _ in range(2):
        response = client.post('/api/v1/mdm/demo/examples')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'READY'
        assert response.json()['sample_groups'] == 8
    again = read(client, run_id)
    assert (again['workflow_revision'], again['decision_revision']) == revisions


def test_backfill_existing_demo_preserves_review_and_is_scoped(client, monkeypatch):
    enable(monkeypatch)
    assert client.post('/api/v1/mdm/demo/examples').status_code == 403
    enter(client)
    run_id = client.post('/api/v1/mdm/demo/start').json()['run_id']
    from masterone.mdm import demo_examples
    original = demo_examples.prepare_examples
    monkeypatch.setattr(demo_examples, 'prepare_examples', lambda *args: {'status': 'WAITING'})
    run_one(client)
    run_one(client)
    monkeypatch.setattr(demo_examples, 'prepare_examples', original)
    with client.session_factory.begin() as db:
        from masterone.mdm.models import ResolutionRecord
        record = db.query(ResolutionRecord).filter_by(run_id=run_id, external_id='CRM-0021').one()
        pair = next(p for p in db.query(WorkflowPair).filter_by(run_id=run_id)
                    if record.id in (p.baseline['left_id'], p.baseline['right_id']))
        pair.decision, pair.revision = 'REJECTED', 1
        veto_id = pair.id
    # An older demo may already have a published singleton generation.
    state = read(client, run_id)
    from tests.test_mdm.test_workflow_contract import request
    old = client.post(f'/api/v1/mdm/runs/{run_id}/workflow/cluster-queue',
        json=request(state['workflow_revision'], expected_decision_revision=state['decision_revision'], queue_mode='all'))
    assert old.status_code == 202, old.text
    run_one(client)
    assert read(client, run_id)['cluster_revision'] == 1
    result = client.post('/api/v1/mdm/demo/examples')
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'PREPARING'
    assert result.json()['sample_groups'] == 7
    assert client.post('/api/v1/mdm/demo/examples').json()['status'] == 'PREPARING'
    with client.session_factory.begin() as db:
        receipt = db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='DEMO_PRESET').one()
        child = db.get(WorkflowOperation, receipt.result['cluster_refresh_operation_id'])
        child.status = 'RETRYABLE_FAILED'
        from masterone.mdm.models import ResolutionTask
        db.query(ResolutionTask).filter_by(run_id=run_id).one().status = 'RETRYABLE_FAILED'
    assert client.post('/api/v1/mdm/demo/examples').json()['status'] == 'FAILED'
    with client.session_factory.begin() as db:
        db.get(WorkflowOperation, child.id).status = 'QUEUED'
        db.query(ResolutionTask).filter_by(run_id=run_id).one().status = 'QUEUED'
    run_one(client)
    assert client.post('/api/v1/mdm/demo/examples').json()['status'] == 'READY'
    assert read(client, run_id)['cluster_revision'] == 2
    with client.session_factory() as db:
        assert db.get(WorkflowPair, veto_id).decision == 'REJECTED'
        assert db.get(WorkflowPair, veto_id).revision == 1
        assert db.query(StewardFeedback).filter_by(run_id=run_id).count() == 0
        assert db.query(WorkflowOperation).filter_by(run_id=run_id, operation_type='DEMO_PRESET').count() == 1


def test_normal_run_never_gets_preset_decisions(client, workflow_run):
    from tests.test_mdm.test_workflow_stages import ready_decisions
    ready_decisions(client, workflow_run)
    from masterone.mdm.demo_examples import prepare_examples
    with client.session_factory.begin() as db:
        result = prepare_examples(db, db.get(ResolutionRun, workflow_run))
        assert result['status'] == 'NOT_APPLICABLE'
        assert db.query(WorkflowPair).filter_by(run_id=workflow_run, decision='ACCEPTED').count() == 0
