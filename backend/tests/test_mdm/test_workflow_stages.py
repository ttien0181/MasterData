from pathlib import Path
from unittest.mock import Mock

import pytest
from masterone.mdm.job_queue import claim_next, ownership
from masterone.mdm.models import ResolutionProposal, ResolutionTask
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowPair

from tests.test_mdm.test_workflow_contract import request


def run_one(client):
    from masterone.mdm.workflow_jobs import execute_workflow
    claim = claim_next(client.session_factory, 'workflow-test', lease_seconds=300)
    assert claim
    with client.session_factory() as db:
        op_id = db.get(ResolutionTask, claim.task_id).operation_id
    with ownership(claim):
        execute_workflow(op_id, client.session_factory, lambda *_: None)
    return op_id


def read(client, run_id):
    return client.get(f'/api/v1/mdm/runs/{run_id}/workflow').json()


def test_prepare_reports_monotonic_progress_and_unlocks_decide_only_after_publication(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowOperation
    from masterone.mdm.job_queue import progress

    assert 'start-decide' not in read(client, workflow_run)['allowed_actions']
    claim = claim_next(client.session_factory, 'progress-test', lease_seconds=300)
    with client.session_factory() as db:
        op_id = db.get(ResolutionTask, claim.task_id).operation_id
    updates = []

    def report(phase, done, total):
        assert done < total  # 100% is reserved for the committed DATA_READY state.
        updates.append((phase, done, total))
        progress(client.session_factory, claim, phase, done, total)

    with ownership(claim):
        execute_workflow(op_id, client.session_factory, report)
    assert updates[0][1] == 0
    assert [item[1] for item in updates] == sorted(item[1] for item in updates)
    assert {'prepare-read', 'prepare-normalize', 'prepare-publish'} <= {item[0] for item in updates}
    result = read(client, workflow_run)
    assert result['workflow_state'] == 'DATA_READY'
    assert 'start-decide' in result['allowed_actions']
    with client.session_factory() as db:
        op = db.get(WorkflowOperation, op_id)
        assert op.status == 'COMPLETED'
        assert op.progress['processed'] == op.progress['total']


def test_decide_persists_pair_counters_through_assessment(client, workflow_run):
    from masterone.mdm.workflow_jobs import execute_workflow
    from masterone.mdm.workflow_models import WorkflowOperation
    from masterone.mdm.job_queue import progress
    run_one(client)
    state = read(client, workflow_run)
    started = client.post(f'/api/v1/mdm/runs/{workflow_run}/workflow/start-decide', json=request(state['workflow_revision']))
    assert started.status_code == 202
    # Reading progress before the first candidate exists must remain valid.
    assert read(client, workflow_run)['workflow_state'] == 'DECIDE_RUNNING'
    claim = claim_next(client.session_factory, 'decision-progress-test', lease_seconds=300)
    with ownership(claim):
        execute_workflow(started.json()['operation_id'], client.session_factory,
                         lambda phase, done, total: progress(client.session_factory, claim, phase, done, total))
    final = read(client, workflow_run)
    with client.session_factory() as db:
        operation = db.get(WorkflowOperation, started.json()['operation_id'])
        assert operation.status == 'COMPLETED'
        assert operation.progress['candidate_count'] == final['counts']['pairs']['value']
        assert operation.progress['classified_count'] == final['counts']['pairs']['value']
        assert operation.progress['classification_started'] is True
        assert operation.progress['processed'] == operation.progress['total'] == final['counts']['pairs']['value']


def ready_decisions(client, run_id):
    run_one(client)
    state = read(client, run_id)
    response = client.post(f'/api/v1/mdm/runs/{run_id}/workflow/start-decide', json=request(state['workflow_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    return read(client, run_id)


@pytest.mark.parametrize('stream,resume', [(False, False), (False, True), (True, False), (True, True)])
def test_all_v2_paths_stop_before_cluster(client, workflow_run, monkeypatch, tmp_path, stream, resume):
    from masterone.config import get_settings
    from masterone.mdm.engine_adapter import STREAMING_FEATURES
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.pipeline.golden_v2 import record_payload
    from masterone.mdm.pipeline.stage_runner import StageRunner
    spy = Mock(side_effect=AssertionError('CLUSTER CALLED BEFORE FINALIZE'))
    monkeypatch.setattr('masterone.mdm.resolution_service.build_proposals', spy)
    monkeypatch.setattr('masterone.mdm.pipeline.stage_runner.build_proposals', spy)
    monkeypatch.setattr('masterone.mdm.pipeline.clustering.build_proposals', spy)
    monkeypatch.setattr('masterone.mdm.pipeline.golden.build_golden', spy)
    monkeypatch.setattr(get_settings(), 'semantic_address_enabled', False)
    monkeypatch.setattr(get_settings(), 'pipeline_resume_enabled', resume)
    for name in STREAMING_FEATURES:
        monkeypatch.setattr(get_settings(), name, stream)
    state = ready_decisions(client, workflow_run)
    assert state['workflow_state'] == 'DECIDE_REVIEW'
    with client.session_factory() as db:
        assert db.query(WorkflowPair).filter_by(run_id=workflow_run).count() > 0
        assert db.query(WorkflowCluster).count() == 0
        assert db.query(ResolutionProposal).filter_by(run_id=workflow_run).count() == 0
        rows = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run)]
    runner = StageRunner(workflow_run, 'direct', output_dir=Path(tmp_path), db_url=str(client.session_factory.kw['bind'].url))
    try:
        output = runner.run_decide(rows)
        assert not output['proposals']
    finally:
        runner.close_snapshot()
        runner.con.close()
    assert client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/data').status_code == 200
    assert client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/pairs?limit=1').json()['total'] > 0
    assert client.post(f'/api/v1/mdm/runs/{workflow_run}/start').status_code == 409
    assert spy.call_count == 0


def test_workflow_review_overlay_replay_and_bulk(client, workflow_run):
    state = ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = client.get(url + '/pairs').json()['items'][0]
    baseline = pair['baseline']
    body = request(state['workflow_revision'], expected_pair_revision=pair['revision'], decision='no_merge')
    first = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body)
    assert first.status_code == 200, first.text
    assert client.post(url + f"/pairs/{pair['pair_id']}/decision", json=body).json() == first.json()
    assert first.json()['baseline'] == baseline
    assert first.json()['decision'] == 'REJECTED'
    assert client.post(url + f"/pairs/{pair['pair_id']}/decision", json={**body, 'request_id': 'stale'}).status_code == 409
    state = read(client, workflow_run)
    response = client.post(url + '/bulk-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], decision='merge', scope={'filter': {}}))
    assert response.status_code == 202, response.text
    assert client.post(url + '/finalize-decisions', json=request(read(client, workflow_run)['workflow_revision'],
        expected_decision_revision=state['decision_revision'])).status_code == 409
    run_one(client)
    op = client.get(url + '/operations/' + response.json()['operation_id']).json()
    assert op['progress']['skipped'] >= 1
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0
