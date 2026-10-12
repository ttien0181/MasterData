from copy import deepcopy

import pytest
from masterone.mdm.generation_staging import digest
from masterone.mdm.job_queue import claim_next, ownership
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowOperation

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def finalize(client, run_id):
    state = read(client, run_id)
    return client.post(f'/api/v1/mdm/runs/{run_id}/workflow/finalize-decisions',
                       json=request(state['workflow_revision'], expected_decision_revision=state['decision_revision']))


def complete_reviews(client, run_id):
    url = f'/api/v1/mdm/runs/{run_id}/workflow'
    for pair in client.get(url + '/pairs').json()['items']:
        if pair['baseline']['decision'] not in ('MERGEABLE', 'NON_MATCH', 'NOT_MERGEABLE', 'BLOCKED'):
            state = read(client, run_id)
            response = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'],
                expected_pair_revision=pair['revision'], decision='no_merge'))
            assert response.status_code == 200, response.text


def test_workflow_finalize_snapshot_replay_singleton(client, workflow_run):
    assert finalize(client, workflow_run).status_code == 409
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'])
    response = client.post(url + '/finalize-decisions', json=body)
    assert response.status_code == 202, response.text
    assert client.post(url + '/finalize-decisions', json=body).json() == response.json()
    assert client.post(url + '/finalize-decisions', json={**body, 'request_id': 'second-tab'}).status_code == 409
    assert client.get(url + '/clusters').status_code == 409
    with client.session_factory() as db:
        op = db.get(WorkflowOperation, response.json()['operation_id'])
        snapshot = deepcopy(db.get(WorkflowArtifact, op.artifact_id).payload)
        assert digest(snapshot) == db.get(WorkflowArtifact, op.artifact_id).content_hash
    run_one(client)
    assert read(client, workflow_run)['workflow_state'] == 'GOLDEN_REVIEW'
    items = client.get(url + '/clusters').json()['items']
    members = [r for c in items for r in c['member_record_ids']]
    assert len(members) == len(set(members)) == 3
    assert any(len(c['member_record_ids']) == 1 for c in items)
    with client.session_factory() as db:
        assert db.get(WorkflowArtifact, op.artifact_id).payload == snapshot


def test_workflow_corrupt_snapshot_and_expired_fence(client, workflow_run):
    from datetime import UTC, datetime, timedelta

    from masterone.mdm.job_queue import OwnershipLostError
    from masterone.mdm.models import ResolutionJobAttempt
    from masterone.mdm.workflow_jobs import execute_workflow
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = finalize(client, workflow_run)
    assert response.status_code == 202
    claim = claim_next(client.session_factory, 'expired', lease_seconds=30)
    with client.session_factory.begin() as db:
        db.get(ResolutionJobAttempt, claim.attempt_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with ownership(claim), pytest.raises(OwnershipLostError):
        execute_workflow(response.json()['operation_id'], client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0


def test_bridge_conflict_preserves_baseline_guard(tmp_path):
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import cluster_snapshot

    from tests.test_mdm.test_safe_clustering import chain, edge
    a, b, c = chain()
    pairs = [edge(a, b, 15), edge(b, c, 14)]
    pairs[0]['decision'] = pairs[1]['decision'] = 'REVIEW'
    snapshot = {'records': [a, b, c], 'pairs': pairs,
                'overlays': {p['id']: 'ACCEPTED' for p in pairs},
                'negative_pairs': [], 'policy_version': 'person-v3'}
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True, generation_staging_enabled=False)):
        result = cluster_snapshot(snapshot, 'run')
    assert result['conflicts']
    assert not result['clusters']
