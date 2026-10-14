from types import SimpleNamespace

import pytest
from masterone.auth.models import User
from masterone.mdm.models import ResolutionRun
from masterone.mdm.workflow_models import WorkflowCluster

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_golden import ready_golden
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


@pytest.mark.parametrize('role', ['VIEWER', 'AUDITOR', 'POLICY_ADMIN'])
def test_explanation_requires_explain_capability(client, workflow_run, role):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = client.get(url + '/pairs').json()['items'][0]
    state = read(client, workflow_run)
    with client.session_factory.begin() as db:
        db.get(User, db.get(ResolutionRun, workflow_run).owner_user_id).role = role
    assert client.post(url + f"/pairs/{pair['pair_id']}/explanation", json=request(state['workflow_revision'])).status_code == 403


@pytest.mark.parametrize('change', ['role', 'active', 'owner', 'disabled'])
def test_queued_resolution_rechecks_authorization(client, workflow_run, monkeypatch, change):
    from masterone.config import get_settings
    ready_golden(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    s = read(client, workflow_run)
    body = request(s['workflow_revision'], expected_cluster_revision=s['cluster_revision'],
                   expected_golden_revision=s['golden_revision'], expected_decision_revision=s['decision_revision'])
    assert client.post(url + '/golden/resolve', json=body).status_code == 202
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        actor = db.get(User, run.owner_user_id)
        if change == 'role':
            actor.role = 'VIEWER'
        elif change == 'active':
            actor.is_active = False
        elif change == 'owner':
            run.owner_user_id = 'different-owner'
    if change == 'disabled':
        monkeypatch.setattr(get_settings(), 'session_workflow_enabled', False)
    with pytest.raises(ValueError, match='WORKFLOW_AUTHORIZATION_CHANGED'):
        run_one(client)
    with client.session_factory() as db:
        assert not any(c.confirmed for c in db.query(WorkflowCluster))


@pytest.mark.parametrize('change', ['role', 'config'])
def test_cluster_publication_rechecks_after_compute(client, workflow_run, monkeypatch, change):
    from masterone.mdm import workflow_finalize
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    assert finalize(client, workflow_run).status_code == 202
    original = workflow_finalize.cluster_snapshot
    def compute(*args, **kwargs):
        output = original(*args, **kwargs)
        if change == 'role':
            with client.session_factory.begin() as db:
                db.get(User, db.get(ResolutionRun, workflow_run).owner_user_id).role = 'VIEWER'
        else:
            monkeypatch.setattr(workflow_finalize, 'config_identity', lambda *_: {'changed': True})
        return output
    monkeypatch.setattr(workflow_finalize, 'cluster_snapshot', compute)
    with pytest.raises(ValueError, match='WORKFLOW_AUTHORIZATION_CHANGED' if change == 'role' else 'WORKFLOW_CONFIG_CHANGED'):
        run_one(client)
    with client.session_factory() as db:
        assert db.get(ResolutionRun, workflow_run).cluster_revision == 0
        assert db.query(WorkflowCluster).count() == 0


def test_repair_confirmation_scoped_and_undo_requires_review():
    from masterone.mdm.golden_service import repair_confirmation
    row = SimpleNamespace(payload={'member_record_ids': ['safe'], 'source_snapshot_hash': 'same-source'}, selections={}, golden={'fields': {}})
    old = SimpleNamespace(payload=row.payload, selections={}, golden=row.golden, confirmed=False)
    assert not repair_confirmation(row, [old], {'auto_confirm_record_ids': ['conflict']})
    old.confirmed = True
    assert repair_confirmation(row, [old], {'auto_confirm_record_ids': ['conflict']})
    row.payload = {'member_record_ids': ['conflict']}
    assert repair_confirmation(row, [], {'auto_confirm_record_ids': ['conflict']})
    assert not repair_confirmation(row, [], {'auto_confirm_record_ids': [], 'repair_record_ids': ['conflict']})
    row.payload['requires_manual_review'] = ['name']
    assert not repair_confirmation(row, [], {'auto_confirm_record_ids': ['conflict']})


def test_pending_manual_review_survives_regroup():
    from masterone.mdm.golden_service import carry_choices
    old = SimpleNamespace(payload={'member_record_ids':['a'],'requires_manual_review':['name']}, selections={})
    new = SimpleNamespace(payload={'member_record_ids':['a']})
    carry_choices(None, None, new, previous_clusters=[old])
    assert new.payload['requires_manual_review'] == ['name']


def test_resolution_uses_current_generation_snapshot_despite_clock_order(client, workflow_run):
    from copy import deepcopy
    from datetime import timedelta

    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
    ready_golden(client, workflow_run)
    with client.session_factory.begin() as db:
        prior = db.query(WorkflowOperation).filter_by(run_id=workflow_run, operation_type='CLUSTER').one()
        wrong = deepcopy(db.get(WorkflowArtifact, prior.artifact_id).payload)
        wrong['decision_revision'] += 7
        artifact = WorkflowArtifact(run_id=workflow_run, phase='DECISION_SNAPSHOT', payload=wrong, content_hash=digest(wrong))
        db.add(artifact)
        db.flush()
        db.add(WorkflowOperation(run_id=workflow_run, operation_type='CLUSTER', request_id='clock-decoy',
            payload={}, payload_hash=digest({}), status='COMPLETED', actor_user_id=prior.actor_user_id,
            created_at=prior.created_at + timedelta(days=1), artifact_id=artifact.id))
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    s = read(client,workflow_run)
    body = request(s['workflow_revision'], expected_cluster_revision=s['cluster_revision'],
                   expected_golden_revision=s['golden_revision'], expected_decision_revision=s['decision_revision'])
    response = client.post(url+'/golden/resolve',json=body)
    assert response.status_code == 202
    run_one(client)
    assert client.get(url+f"/operations/{response.json()['operation_id']}").json()['status'] == 'COMPLETED'


def test_bulk_job_rejects_revoked_permission(client, workflow_run):
    ready_decisions(client,workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    s = read(client,workflow_run)
    response = client.post(url+'/bulk-decisions', json=request(s['workflow_revision'],
        expected_decision_revision=s['decision_revision'], decision='no_merge', scope={'filter':{}}))
    assert response.status_code == 202
    with client.session_factory.begin() as db:
        db.get(User,db.get(ResolutionRun,workflow_run).owner_user_id).role = 'VIEWER'
    with pytest.raises(ValueError,match='WORKFLOW_AUTHORIZATION_CHANGED'):
        run_one(client)
    assert read(client,workflow_run)['decision_revision'] == s['decision_revision']
