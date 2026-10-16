"""Real rebuild lifecycle, retained history and publication fences."""
from copy import deepcopy
from uuid import uuid4

import pytest
from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowPair

from tests.test_mdm.test_matching_foundation import REGISTRY, new_run, review
from tests.test_mdm.test_matching_integration import fake_encoder, hybrid_profile  # noqa: F401
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one

pytestmark = pytest.mark.usefixtures('fake_encoder')


def rebuild_body(client, run, profile):
    state = read(client, run)
    return {'request_id': str(uuid4()), 'expected_workflow_revision': state['workflow_revision'],
            'expected_decision_revision': state['decision_revision'], 'profile_id': profile['id'],
            'workload_plan_hash': digest({'profile_digest': profile['digest'], 'budget': profile['config']['execution_budget']})}


@pytest.mark.parametrize('action,decision', [('merge', 'ACCEPTED'), ('no_merge', 'REJECTED'), ('deny', 'NON_MATCH')])
def test_rebuild_retains_review_ids_baselines_edges_feedback_and_retry(client, monkeypatch, action, decision):
    from masterone.mdm.matching_foundation_models import MatchingBinding, StewardFeedback
    from masterone.mdm.models import ResolutionNegativeEdge
    profile = hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    response, _, _, pair = review(client, run, action)
    assert response.status_code == 200
    with client.session_factory() as db:
        original = {p.id: (deepcopy(p.baseline), p.decision, p.revision, p.review_reason)
                    for p in db.query(WorkflowPair).filter_by(run_id=run)}
        bindings = db.query(MatchingBinding).filter_by(run_id=run).count()
        feedback = db.query(StewardFeedback).filter_by(run_id=run).count()
        edges = db.query(ResolutionNegativeEdge).filter_by(run_id=run).count()
    body = rebuild_body(client, run, profile)
    endpoint = REGISTRY + f'/runs/{run}/rebuild-candidates'
    assert client.post(endpoint, json=body, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert client.post(endpoint, json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post(endpoint, json=body)
    assert response.status_code == 202, response.text
    replay = client.post(endpoint, json=body)
    assert replay.status_code == 202 and replay.json()['operation_id'] == response.json()['operation_id']
    run_one(client)
    with client.session_factory() as db:
        assert {p.id: (p.baseline, p.decision, p.revision, p.review_reason)
                for p in db.query(WorkflowPair).filter_by(run_id=run)} == original
        assert db.get(WorkflowPair, pair['pair_id']).decision == decision
        assert db.query(StewardFeedback).filter_by(run_id=run).count() == feedback
        assert db.query(ResolutionNegativeEdge).filter_by(run_id=run).count() == edges
        assert db.query(MatchingBinding).filter_by(run_id=run).count() == bindings + 2
    receipt = client.get(f"/api/v1/mdm/runs/{run}/workflow/operations/{response.json()['operation_id']}").json()
    assert receipt['operation_type'] == 'REBUILD_CANDIDATES' and receipt['status'] == 'COMPLETED'
    assert client.post(endpoint, json=body).json()['operation_id'] == response.json()['operation_id']


def test_concurrent_review_revision_prevents_rebuild_publication(client, monkeypatch):
    from masterone.mdm.models import ResolutionRun
    profile = hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    response = client.post(REGISTRY + f'/runs/{run}/rebuild-candidates', json=rebuild_body(client, run, profile))
    assert response.status_code == 202, response.text
    from masterone.mdm.matching_agents import candidate_union
    original_build = candidate_union.build_union
    def concurrent(*args, **kwargs):
        result = original_build(*args, **kwargs)
        with client.session_factory.begin() as db:
            current = db.get(ResolutionRun, run)
            current.decision_revision += 1
        return result
    monkeypatch.setattr(candidate_union, 'build_union', concurrent)
    with pytest.raises(ValueError, match='REVISION_CHANGED'):
        run_one(client)


def test_failed_target_profile_never_becomes_feedback_lineage(client, monkeypatch):
    from masterone.mdm.matching_foundation_models import StewardFeedback
    from masterone.mdm.matching_foundation_service import latest_binding
    from masterone.mdm.models import ResolutionTask
    from masterone.mdm.workflow_models import WorkflowOperation
    first = hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    second = client.post(REGISTRY + '/profiles', json={'version': 2, 'integration': True}).json()
    assert client.post(REGISTRY + f"/profiles/{second['id']}/validate", json={'expected_revision': 1}).status_code == 200
    response = client.post(REGISTRY + f'/runs/{run}/rebuild-candidates', json=rebuild_body(client, run, second))
    assert response.status_code == 202, response.text
    with client.session_factory.begin() as db:
        op = db.get(WorkflowOperation, response.json()['operation_id'])
        op.status = 'FAILED'
        db.query(ResolutionTask).filter_by(run_id=run).one().status = 'FAILED'
    with client.session_factory() as db:
        assert latest_binding(db, run).profile_id == first['id']
    response, _, _, _ = review(client, run, 'merge')
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        assert db.query(StewardFeedback).filter_by(run_id=run).one().profile_id == first['id']


def test_rebuild_union_retains_pair_not_retrieved_and_preserves_revision():
    from masterone.mdm.matching_agents.candidate_union import build_union

    from tests.test_mdm.test_matching_candidates import config, records
    cfg = config()
    cfg.update(experiment='E1')
    cfg['retrieval']['tfidf'] = {'top_k': 10, 'min_similarity': .30}
    historical = {'id': 'accepted-historical', 'left_id': 'r0000', 'right_id': 'r0002'}
    result = build_union(records(3), cfg, None, [historical], lambda *_: None)
    assert result.metrics['retrieved_current'] == 0 and len(result.pairs) == 1
    assert result.pairs['r0000', 'r0002'] == {'retained_history'}


def test_rebuild_workload_or_owner_rejected(client, monkeypatch):
    profile = hybrid_profile(client, monkeypatch)
    run = new_run(client)
    ready_decisions(client, run)
    body = rebuild_body(client, run, profile)
    endpoint = REGISTRY + f'/runs/{run}/rebuild-candidates'
    assert client.post(endpoint, json={**body, 'workload_plan_hash': '0' * 64}).status_code == 409
    credentials = {'email': 'm2b-viewer@example.com', 'password': 'm2b-other-user-password'}
    assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
    client.headers['X-CSRF-Token'] = client.post('/api/v1/auth/login', json=credentials).json()['csrf_token']
    assert client.post(endpoint, json=body).status_code == 403
