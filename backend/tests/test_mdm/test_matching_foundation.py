"""M1 governance integration: real API/worker, no alternate matcher or fake gold."""
from copy import deepcopy
from uuid import uuid4

import pytest
from masterone.auth.models import User
from masterone.auth.security import ROLE_CAPABILITIES
from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.matching_foundation_models import MatchingBinding, StewardFeedback
from masterone.mdm.models import ResolutionAuditEvent, ResolutionRecord
from masterone.mdm.workflow_models import WorkflowPair
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one

REGISTRY = '/api/v1/mdm/matching'
CSV = b'source_record_id,source_system,name,phone,email,address\n1,CRM,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n2,BILL,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n3,CRM,Tran Thi Binh,0987654321,b@example.test,456 HCM\n'


def flags(monkeypatch, *, profile=True, feedback=True):
    for key, value in {'session_workflow_enabled': True, 'matching_profile_registry_enabled': profile,
                       'steward_feedback_capture_enabled': feedback}.items():
        monkeypatch.setattr(get_settings(), key, value)


def new_run(client):
    response = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
                           files={'files': ('m1.csv', CSV, 'text/csv')})
    assert response.status_code == 201, response.text
    return response.json()['run_id']


def profile(client, version=1, apply=True):
    result = client.post(REGISTRY + '/profiles', json={'version': version})
    assert result.status_code == 201, result.text
    row = result.json()
    validated = client.post(REGISTRY + f"/profiles/{row['id']}/validate", json={'expected_revision': 1})
    assert validated.status_code == 200, validated.text
    if apply:
        result = client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': row['id'], 'expected_revision': 0})
        assert result.status_code == 200, result.text
    return row


def review(client, run, decision, *, pair_id=None, amend=False, request_id=None):
    root = f'/api/v1/mdm/runs/{run}/workflow'
    pairs = client.get(root + '/pairs').json()['items']
    pair = next(p for p in pairs if p['pair_id'] == pair_id) if pair_id else next(p for p in pairs if p['eligibility']['manual_allowed'])
    state = read(client, run)
    body = request(state['workflow_revision'], request_id=request_id or str(uuid4()),
                   expected_pair_revision=pair['revision'], decision=decision)
    if amend:
        body['reason'] = 'Explicit correction of prior human decision'
    endpoint = root + f"/pairs/{pair['pair_id']}/" + ('amend' if amend else 'decision')
    return client.post(endpoint, json=body), endpoint, body, pair


def test_r1_capabilities_are_explicit_and_do_not_open_policy_or_audit_roles():
    new = {'learn_match_profile', 'apply_match_profile', 'manage_entity_structure'}
    assert new <= ROLE_CAPABILITIES['STEWARD']
    assert new <= ROLE_CAPABILITIES['SENIOR_STEWARD']
    for role in ('VIEWER', 'AUDITOR', 'POLICY_ADMIN'):
        assert not new & ROLE_CAPABILITIES[role]


def test_new_flags_default_false_and_legacy_run_has_no_binding(client, workflow_run):
    assert not get_settings().matching_profile_registry_enabled
    assert not get_settings().steward_feedback_capture_enabled
    assert not get_settings().advanced_evaluation_registry_enabled
    ready_decisions(client, workflow_run)
    with client.session_factory() as db:
        assert db.query(MatchingBinding).count() == 0
        assert db.query(StewardFeedback).count() == 0
    assert client.get(REGISTRY + '/profiles').status_code == 404


def test_profile_default_only_applies_to_new_runs_and_worker_pins_revision(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    flags(monkeypatch)
    p1 = profile(client)
    assert client.get(REGISTRY + f'/runs/{workflow_run}/binding').json() is None
    run = new_run(client)
    binding1 = client.get(REGISTRY + f'/runs/{run}/binding').json()
    assert binding1['revision'] == 1 and binding1['profile_id'] == p1['id']
    ready_decisions(client, run)
    binding3 = client.get(REGISTRY + f'/runs/{run}/binding').json()
    assert binding3['revision'] == 3 and binding3['profile_digest'] == p1['digest']
    with client.session_factory() as db:
        assert [b.binding_kind for b in db.query(MatchingBinding).filter_by(run_id=run).order_by(MatchingBinding.revision)] == ['CONFIG_PINNED', 'PREPARED', 'ASSESSED']
    p2 = profile(client, version=2, apply=False)
    changed = client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': p2['id'], 'expected_revision': 1})
    assert changed.status_code == 200
    assert client.get(REGISTRY + f'/runs/{run}/binding').json()['profile_id'] == p1['id']
    assert client.get(REGISTRY + f'/runs/{new_run(client)}/binding').json()['profile_id'] == p2['id']


def test_profile_config_is_immutable_through_raw_sql_and_retention_prevents_delete(client, monkeypatch):
    flags(monkeypatch)
    p = profile(client)
    for statement in ("UPDATE mdm_matching_profiles SET config_json='{}' WHERE id=:id",
                      'DELETE FROM mdm_matching_profiles WHERE id=:id'):
        with pytest.raises(DBAPIError), client.session_factory.begin() as db:
            db.execute(text(statement), {'id': p['id']})
    assert client.get(REGISTRY + f"/profiles/{p['id']}").json()['digest'] == p['digest']
    assert client.post(REGISTRY + '/profiles', json={'version': 1}).status_code == 409


@pytest.mark.parametrize('role', ['VIEWER', 'AUDITOR', 'POLICY_ADMIN'])
def test_registry_mutations_deny_non_steward_roles(client, monkeypatch, role):
    flags(monkeypatch)
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = role
    assert client.post(REGISTRY + '/profiles', json={'version': 1}).status_code == 403


def test_registry_checks_csrf_origin_and_cross_owner(client, monkeypatch):
    flags(monkeypatch)
    p = profile(client)
    assert client.post(REGISTRY + '/profiles', json={'version': 2}, headers={'X-CSRF-Token': 'bad'}).status_code == 403
    assert client.post(REGISTRY + '/profiles', json={'version': 2}, headers={'Origin': 'https://evil.example'}).status_code == 403
    credentials = {'email': 'other-m1@example.com', 'password': 'another-long-test-password'}
    assert client.post('/api/v1/auth/register', json=credentials).status_code == 201
    logged = client.post('/api/v1/auth/login', json=credentials)
    client.headers['X-CSRF-Token'] = logged.json()['csrf_token']
    assert client.get(REGISTRY + f"/profiles/{p['id']}").status_code == 404


@pytest.mark.parametrize('decision,label', [('merge', 'MATCH'), ('deny', 'NON_MATCH'), ('no_merge', None), ('defer', None)])
def test_direct_review_maps_only_explicit_binary_decisions_and_retry_is_idempotent(client, workflow_run, monkeypatch, decision, label):
    flags(monkeypatch, profile=False)
    ready_decisions(client, workflow_run)
    first, endpoint, body, _ = review(client, workflow_run, decision)
    assert first.status_code == 200, first.text
    assert client.post(endpoint, json=body).json() == first.json()
    rows = client.get(REGISTRY + f'/runs/{workflow_run}/feedback').json()
    assert len(rows) == 1 and rows[0]['label'] == label
    assert rows[0]['origin'] == 'HUMAN_DIRECT'
    assert rows[0]['trainability_status'] == ('TRAINABLE' if label else 'NOT_TRAINABLE')
    with client.session_factory() as db:
        event = db.query(StewardFeedback).one()
        assert digest(event.feature_snapshot_json) == event.feature_snapshot_hash
        assert event.feature_snapshot_json['fields']
        assert all('left' not in f and 'right' not in f and 'matched_values' not in f for f in event.feature_snapshot_json['fields'])
        assert 'human_decision' not in event.feature_snapshot_json


def test_amend_supersedes_prior_positive_without_converting_rejection_to_negative(client, workflow_run, monkeypatch):
    flags(monkeypatch, profile=False)
    ready_decisions(client, workflow_run)
    first, _, _, pair = review(client, workflow_run, 'merge')
    assert first.status_code == 200
    run_one(client)  # Wait for the automatic group refresh before amending a decision.
    amended, _, _, _ = review(client, workflow_run, 'no_merge', pair_id=pair['pair_id'], amend=True)
    assert amended.status_code == 200, amended.text
    with client.session_factory() as db:
        rows = db.query(StewardFeedback).order_by(StewardFeedback.pair_revision).all()
        assert len(rows) == 2
        assert rows[0].human_direct_label == 'MATCH'
        assert rows[1].human_direct_label is None and rows[1].supersedes_feedback_id == rows[0].id
        assert db.query(ResolutionAuditEvent).filter_by(action='FEEDBACK_SUPERSEDED').count() == 1


def test_worker_rejects_pinned_profile_revision_corruption(client, monkeypatch):
    from masterone.mdm.workflow_models import WorkflowOperation
    flags(monkeypatch)
    profile(client)
    run = new_run(client)
    with client.session_factory.begin() as db:
        op = db.query(WorkflowOperation).filter_by(run_id=run, operation_type='PREPARE').one()
        op.payload = {**op.payload, 'matching_binding_revision': 999}
    with pytest.raises(ValueError, match='MATCHING_BINDING_IDENTITY_CHANGED'):
        run_one(client)


def test_rule_output_equivalence_with_registry_and_feedback_flags_off(client, monkeypatch):
    flags(monkeypatch, profile=False, feedback=False)
    first = new_run(client)
    ready_decisions(client, first)
    def baseline(run):
        with client.session_factory() as db:
            ids = {r.id: (r.source_system, r.external_id) for r in db.query(ResolutionRecord).filter_by(run_id=run)}
            return {tuple(sorted((ids[p.baseline['left_id']], ids[p.baseline['right_id']]))):
                {k: v for k, v in deepcopy(p.baseline).items() if k not in {'id', 'left_id', 'right_id'}}
                for p in db.query(WorkflowPair).filter_by(run_id=run)}
    frozen = baseline(first)
    flags(monkeypatch, profile=True, feedback=False)
    profile(client)
    second = new_run(client)
    ready_decisions(client, second)
    assert baseline(second) == frozen
    assert baseline(first) == frozen
    with client.session_factory() as db:
        assert db.query(StewardFeedback).count() == 0


def test_dataset_registration_does_not_self_certify_truth(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'advanced_evaluation_registry_enabled', True)
    with client.session_factory() as db:
        owner = db.query(User).filter_by(email_normalized='steward@example.com').one().id
    body = {'name': 'diagnostic', 'version': 1, 'provenance_kind': 'SYNTHETIC',
        'label_schema_version': 'synthetic_pair_v1', 'manifest_uri': owner + '/datasets/manifest.json',
        'manifest_hash': 'a' * 64, 'label_snapshot_hash': 'b' * 64}
    response = client.post(REGISTRY + '/datasets', json=body)
    assert response.status_code == 201, response.text
    assert response.json()['truth_verification_status'] == 'NOT_VERIFIED'
    assert response.json()['trainability_status'] == 'NOT_TRAINABLE'
    assert client.post(REGISTRY + '/datasets', json={**body, 'version': 2, 'manifest_uri': '../private'}).status_code == 422


def test_explicit_bulk_review_records_human_events_once(client, workflow_run, monkeypatch):
    flags(monkeypatch, profile=False)
    ready_decisions(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pairs = client.get(root + '/pairs').json()['items']
    state = read(client, workflow_run)
    body = request(state['workflow_revision'], expected_decision_revision=state['decision_revision'],
                   decision='deny', scope={'pair_ids': [p['pair_id'] for p in pairs]}, auto_only=False)
    result = client.post(root + '/bulk-decisions', json=body)
    assert result.status_code == 202, result.text
    run_one(client)
    assert client.post(root + '/bulk-decisions', json=body).status_code == 202
    with client.session_factory() as db:
        rows = db.query(StewardFeedback).filter_by(run_id=workflow_run).all()
        assert len(rows) == len(pairs)
        assert all(r.human_direct_label == 'NON_MATCH' and r.origin == 'HUMAN_BULK_DIRECT' for r in rows)


def test_amend_snapshot_precedes_negative_assessment_refresh(client, workflow_run, monkeypatch):
    from masterone.mdm.steward_feedback import capture_review_snapshot
    flags(monkeypatch, profile=False)
    ready_decisions(client, workflow_run)
    denied, _, _, pair = review(client, workflow_run, 'deny')
    assert denied.status_code == 200
    state = read(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    assert client.post(root + '/reassess', json=request(state['workflow_revision'])).status_code == 202
    run_one(client)
    from masterone.mdm.models import ResolutionRun
    with client.session_factory() as db:
        before = capture_review_snapshot(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowPair, pair['pair_id']))
        assert before['negative_derived']
    amended, _, _, _ = review(client, workflow_run, 'merge', pair_id=pair['pair_id'], amend=True)
    assert amended.status_code == 200, amended.text
    with client.session_factory() as db:
        row = db.query(StewardFeedback).filter_by(workflow_pair_id=pair['pair_id'], pair_revision=2).one()
        assert row.feature_snapshot_hash == digest(before['features'])
        assert row.human_direct_label == 'MATCH'
        assert row.trainability_status == 'NOT_TRAINABLE' and row.trainability_reason == 'HITL_DERIVED_FEATURES'


def test_group_inference_does_not_create_human_pair_labels(client, workflow_run, monkeypatch):
    from tests.test_mdm.test_workflow_group_approvals import (
        test_group_approval_api_accepts_internal_pending_pairs_with_audit,
    )
    flags(monkeypatch, profile=False)
    test_group_approval_api_accepts_internal_pending_pairs_with_audit(client, workflow_run)
    with client.session_factory() as db:
        assert db.query(StewardFeedback).count() == 0


def test_automation_does_not_create_human_pair_labels(client, monkeypatch, tmp_path):
    from tests.test_mdm.test_workflow_automation import (
        test_certified_canary_replays_and_publication_keeps_automatic_provenance,
    )
    flags(monkeypatch, profile=False)
    # Existing certificate is a declared QA fixture, never production ground truth.
    test_certified_canary_replays_and_publication_keeps_automatic_provenance(client, monkeypatch, tmp_path)
    with client.session_factory() as db:
        assert db.query(StewardFeedback).count() == 0


def test_raw_sql_cannot_skip_profile_validation_lifecycle(client, monkeypatch):
    flags(monkeypatch)
    response = client.post(REGISTRY + '/profiles', json={'version': 1})
    assert response.status_code == 201
    with pytest.raises(DBAPIError), client.session_factory.begin() as db:
        db.execute(text("UPDATE mdm_matching_profiles SET lifecycle='ACTIVE',lifecycle_revision=2 WHERE id=:id"), {'id': response.json()['id']})
    assert client.get(REGISTRY + '/profiles/' + response.json()['id']).json()['lifecycle'] == 'CANDIDATE'


def test_evaluation_registry_pins_historical_profile_and_marks_unverified_metadata(client, monkeypatch):
    from masterone.mdm.matching_foundation_models import EvaluationReport
    flags(monkeypatch)
    monkeypatch.setattr(get_settings(), 'advanced_evaluation_registry_enabled', True)
    p1 = profile(client)
    run = new_run(client)
    ready_decisions(client, run)
    with client.session_factory() as db:
        owner = db.query(User).filter_by(email_normalized='steward@example.com').one().id
    dataset = client.post(REGISTRY + '/datasets', json={'name': 'm1-diagnostic', 'version': 1,
        'provenance_kind': 'SYNTHETIC', 'label_schema_version': 'synthetic_pair_v1',
        'manifest_uri': owner + '/datasets/manifest.json', 'manifest_hash': 'a' * 64,
        'label_snapshot_hash': 'b' * 64})
    assert dataset.status_code == 201
    body = {'dataset_id': dataset.json()['id'], 'profile_id': p1['id'], 'run_id': run,
        'protocol_version': 'diagnostic-v1', 'protocol_hash': 'c' * 64,
        'report_uri': owner + '/reports/report.json', 'report_hash': 'd' * 64,
        'prediction_manifest_hash': 'e' * 64, 'evaluated_scope': {'independent_truth_verified': True},
        'label_support': {'known': 0}, 'status': 'COMPLETE'}
    result = client.post(REGISTRY + '/evaluations', json=body)
    assert result.status_code == 201, result.text
    assert result.json()['evaluated_scope']['diagnostic_only']
    assert result.json()['evaluated_scope']['independent_truth_verified'] is False
    assert result.json()['evaluated_scope']['artifact_verification'] == 'NOT_PERFORMED_M1_METADATA_REGISTRY'
    p2 = profile(client, version=2, apply=False)
    assert client.put(REGISTRY + '/defaults/owner-private', json={'profile_id': p2['id'], 'expected_revision': 1}).status_code == 200
    assert client.post(REGISTRY + '/evaluations', json={**body, 'profile_id': p2['id']}).status_code == 409
    with client.session_factory() as db:
        row = db.get(EvaluationReport, result.json()['id'])
        assert row.profile_id == p1['id'] and row.profile_digest == p1['digest']
