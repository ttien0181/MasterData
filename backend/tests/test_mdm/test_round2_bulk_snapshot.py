from copy import deepcopy

import pytest
from masterone.config import get_settings
from masterone.mdm.matching_foundation_models import StewardFeedback
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
from sqlalchemy import JSON, event

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


def test_larger_review_batch_resumes_without_duplicate_feedback(client, workflow_run, monkeypatch):
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.workflow_jobs import execute_workflow
    ready_decisions(client, workflow_run)
    monkeypatch.setattr(get_settings(), 'workflow_bulk_batch_rows', 500)
    monkeypatch.setattr(get_settings(), 'steward_feedback_capture_enabled', True)
    ids = [f'round2-{i:04}' for i in range(650)]
    with client.session_factory.begin() as db:
        seed = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        db.add_all(WorkflowPair(id=pid, run_id=workflow_run, baseline=deepcopy(seed.baseline)) for pid in ids)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    response = client.post(root + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='no_merge', scope={'pair_ids': ids}))
    assert response.status_code == 202, response.text
    claim = claim_next(client.session_factory, 'round2-test', lease_seconds=300)
    with client.session_factory() as db:
        oid = db.get(ResolutionTask, claim.task_id).operation_id
    def interrupt(*args):
        raise RuntimeError('after durable checkpoint')
    with ownership(claim):
        with pytest.raises(RuntimeError, match='durable checkpoint'):
            execute_workflow(oid, client.session_factory, interrupt)
        with client.session_factory() as db:
            assert db.get(WorkflowOperation, oid).result['checkpoint']['counts']['processed'] == 500
            assert db.query(StewardFeedback).filter_by(operation_id=oid).count() == 500
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert db.query(StewardFeedback).filter_by(operation_id=oid).count() == 650
        assert all(p.decision == 'REJECTED' and p.revision == 1 for p in db.query(WorkflowPair).filter(WorkflowPair.id.in_(ids)))
        assert db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='FEEDBACK_CAPTURED').count() == 650
        assert db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_PAIR_REJECTED').count() == 650


@pytest.mark.parametrize('assessed', [False, True, 'json-null'])
def test_snapshot_projects_effective_pairs_without_loading_both_jsons(client, workflow_run, monkeypatch, assessed):
    from masterone.mdm.workflow_assessment_reads import effective_result
    from masterone.mdm.workflow_finalize import decision_snapshot
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', assessed is True)
    ready_decisions(client, workflow_run)
    if assessed == 'json-null':
        with client.session_factory.begin() as db:
            db.query(WorkflowPair).filter_by(run_id=workflow_run).update({'assessment': JSON.NULL})
    engine = client.session_factory.kw['bind']
    sql = []
    def collect(_conn, _cursor, statement, *_):
        sql.append(statement.lower())
    with client.session_factory() as db:
        expected = [deepcopy(effective_result(p)) for p in db.query(WorkflowPair).filter_by(run_id=workflow_run).order_by(WorkflowPair.id)]
    event.listen(engine, 'before_cursor_execute', collect)
    try:
        with client.session_factory() as db:
            snapshot = decision_snapshot(db, db.get(ResolutionRun, workflow_run))
    finally:
        event.remove(engine, 'before_cursor_execute', collect)
    assert snapshot['pairs'] == expected
    assert not any('mdm_workflow_pairs.baseline as' in statement for statement in sql)
