from copy import deepcopy

import pytest
from masterone.mdm.matching_foundation_models import StewardFeedback
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
from sqlalchemy import event

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


def test_bulk_insert_failure_rolls_back_pairs_audit_feedback_and_checkpoint(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.workflow_jobs import execute_workflow

    ready_decisions(client, workflow_run)
    monkeypatch.setattr(get_settings(), 'steward_feedback_capture_enabled', True)
    with client.session_factory.begin() as db:
        seed = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        ids = ['r3-rollback-a', 'r3-rollback-b']
        db.add_all(WorkflowPair(id=pid, run_id=workflow_run, baseline=deepcopy(seed.baseline)) for pid in ids)
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='no_merge', scope={'pair_ids': ids})
    response = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/bulk-decisions', json=body)
    assert response.status_code == 202, response.text
    claim = claim_next(client.session_factory, 'round3-rollback', lease_seconds=300)
    with client.session_factory() as db:
        oid = db.get(ResolutionTask, claim.task_id).operation_id
        revision = db.get(ResolutionRun, workflow_run).decision_revision
    engine = client.session_factory.kw['bind']
    def fail(_conn, _cursor, statement, *_):
        if statement.lower().startswith('insert into mdm_resolution_audit_events'):
            raise RuntimeError('round3 injected insert failure')
    event.listen(engine, 'before_cursor_execute', fail)
    try:
        with ownership(claim), pytest.raises(RuntimeError, match='injected insert failure'):
            execute_workflow(oid, client.session_factory, lambda *_: None)
    finally:
        event.remove(engine, 'before_cursor_execute', fail)
    with client.session_factory() as db:
        assert db.get(ResolutionRun, workflow_run).decision_revision == revision
        assert not db.get(WorkflowOperation, oid).result.get('checkpoint')
        assert db.query(StewardFeedback).filter_by(operation_id=oid).count() == 0
        assert db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_PAIR_REJECTED').count() == 0
        assert all(p.decision == 'UNDECIDED' and p.revision == 0 for p in db.query(WorkflowPair).filter(WorkflowPair.id.in_(ids)))
    with ownership(claim):
        execute_workflow(oid, client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert db.query(StewardFeedback).filter_by(operation_id=oid).count() == 2


@pytest.mark.parametrize('memory', [False, True])
def test_decision_store_backends_preserve_guards_and_sealed_barrier(memory):
    from masterone.mdm.pipeline.decision_store import DecisionStore
    store = DecisionStore(memory=memory)
    try:
        pair = {'id': 'edge', 'left_id': 'a', 'right_id': 'b', 'decision': 'MERGEABLE', 'match_weight': 1.0}
        store.add(pair)
        with pytest.raises(ValueError, match='barrier'):
            list(store)
        store.seal()
        assert list(store) == [pair]
        assert store.get('b', 'a') == pair
        store.save_check('b', 'a', 'BLOCKED')
        assert store.check('a', 'b') == 'BLOCKED'
        assert list(store.iter_decision('MERGEABLE')) == [pair]
        with pytest.raises(ValueError, match='sealed'):
            store.add(pair)
    finally:
        store.close()
