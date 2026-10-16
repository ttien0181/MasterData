"""M3 must fail closed without independently verified labels and approved splits."""
import pytest
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.mdm.matching_foundation_models import MatchingProfile, StewardFeedback

from tests.test_mdm.test_matching_foundation import REGISTRY, flags, profile, review
from tests.test_mdm.test_workflow_stages import ready_decisions


def enable(monkeypatch):
    flags(monkeypatch)
    monkeypatch.setattr(get_settings(), 'advanced_evaluation_registry_enabled', True)


def test_learning_without_labels_keeps_profiles_and_binding_unchanged(client, workflow_run, monkeypatch):
    enable(monkeypatch)
    profile(client)
    ready_decisions(client, workflow_run)
    before = client.get(REGISTRY + '/profiles').json()
    response = client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['status'] == 'INSUFFICIENT_LABELS'
    assert value['counts'] == {'MATCH': 0, 'NON_MATCH': 0}
    assert value['profile_created'] is False and value['automation_allowed'] is False
    assert client.get(REGISTRY + '/profiles').json() == before


def test_learning_excludes_superseded_accept_and_rejection_is_not_negative(client, workflow_run, monkeypatch):
    enable(monkeypatch)
    ready_decisions(client, workflow_run)
    first, _, _, pair = review(client, workflow_run, 'merge')
    assert first.status_code == 200
    amended, _, _, _ = review(client, workflow_run, 'no_merge', pair_id=pair['pair_id'], amend=True)
    assert amended.status_code == 200
    response = client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={})
    assert response.status_code == 200, response.text
    assert response.json()['counts'] == {'MATCH': 0, 'NON_MATCH': 0}
    with client.session_factory() as db:
        assert db.query(StewardFeedback).count() == 2
        assert db.query(MatchingProfile).count() == 0


@pytest.mark.parametrize('headers', [{'X-CSRF-Token': 'invalid'}, {'Origin': 'https://evil.example'}])
def test_learning_mutation_checks_csrf_and_origin(client, workflow_run, monkeypatch, headers):
    enable(monkeypatch)
    response = client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={}, headers=headers)
    assert response.status_code == 403


def test_learning_denies_viewer_and_disabled_flag(client, workflow_run, monkeypatch):
    enable(monkeypatch)
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    assert client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={}).status_code == 403


def test_learning_is_unavailable_when_registry_flag_is_off(client, workflow_run):
    assert client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={}).status_code == 404


def test_learning_invalid_dataset_cannot_cross_owner_or_invent_a_split(client, workflow_run, monkeypatch):
    enable(monkeypatch)
    assert client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={'dataset_id': 'unknown'}).status_code == 404
    assert client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={'split': 'random-80-20'}).status_code == 422


def test_structure_or_inferred_revision_does_not_reuse_old_direct_label_as_current(client, workflow_run, monkeypatch):
    from masterone.mdm.workflow_models import WorkflowPair
    enable(monkeypatch)
    ready_decisions(client, workflow_run)
    response, _, _, original = review(client, workflow_run, 'merge')
    assert response.status_code == 200
    with client.session_factory.begin() as db:
        pair = db.get(WorkflowPair, original['pair_id'])
        pair.decision = 'REJECTED'
        pair.revision += 1  # Controlled structure-origin fixture; no new human identity label.
    value = client.post(REGISTRY + f'/runs/{workflow_run}/learn-thresholds', json={}).json()
    assert value['counts']['MATCH'] == 0
    with client.session_factory() as db:
        assert db.query(StewardFeedback).one().human_direct_label == 'MATCH'
