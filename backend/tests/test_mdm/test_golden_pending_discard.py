"""An orphaned extra-field choice needs an explicit, audited dismissal."""
from copy import deepcopy

import pytest
from masterone.mdm.models import ResolutionAuditEvent, ResolutionEntityVersion, ResolutionRun
from masterone.mdm.workflow_models import WorkflowCluster

from tests.test_mdm.test_workflow_golden import golden_request, ready_golden


def orphan(client, run_id):
    groups = ready_golden(client, run_id)
    cid = next(g['cluster_id'] for g in groups if g['member_count'] > 1)
    candidates = [{'selection_mode': 'SOURCE_CHOICE', 'source_record_id': 'removed-source',
                   'value': ['old', 'extra'], 'ancestor_cluster_id': 'previous-group'}]
    with client.session_factory.begin() as db:
        row = db.get(WorkflowCluster, cid)
        row.payload = {**row.payload, 'pending_selections': {'obsolete_extra': candidates,
            'name': [{'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Older name'}]},
            'requires_manual_review': ['obsolete_extra', 'name']}
    return cid, candidates


def test_explicit_discard_keeps_reason_actor_candidates_and_entity_history(client, workflow_run):
    cid, candidates = orphan(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters/{cid}/golden'
    body = golden_request(client, workflow_run, selections={'obsolete_extra': {
        'selection_mode': 'DISCARD_PENDING', 'reason': 'Source removed during split'}})
    response = client.put(root, json=body)
    assert response.status_code == 200, response.text
    assert client.put(root, json=body).json() == response.json()
    current = client.get(root).json()
    assert set(current['pending_selections']) == {'name'}
    assert 'obsolete_extra' not in current['selections']
    assert current['status'] == 'DRAFT'
    assert client.post(root + '/confirm', json=golden_request(client, workflow_run)).status_code == 409
    with client.session_factory() as db:
        row = db.get(WorkflowCluster, cid)
        history = row.payload['discarded_pending_selections']
        assert len(history) == 1
        item = history[0]
        assert item['field'] == 'obsolete_extra'
        assert item['reason'] == 'Source removed during split'
        assert item['candidates'] == candidates
        assert item['actor_user_id'] == db.get(ResolutionRun, workflow_run).owner_user_id
        assert item['created_at']
        latest = db.query(ResolutionEntityVersion).filter_by(entity_id=row.payload['entity_id']).order_by(
            ResolutionEntityVersion.version.desc()).first()
        assert latest.golden_fields['_workflow_choices']['discarded_pending_selections'] == history
        events = db.query(ResolutionAuditEvent).filter_by(run_id=workflow_run, action='WORKFLOW_SAVE_GOLDEN').all()
        assert len(events) == 1
        assert events[0].details['discarded_pending_selections'][0]['candidates'] == candidates
    response = client.put(root, json=golden_request(client, workflow_run, selections={
        'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Reviewed name'}}))
    assert response.status_code == 200, response.text
    assert client.post(root + '/confirm', json=golden_request(client, workflow_run)).status_code == 200


@pytest.mark.parametrize('field,reason', [('obsolete_extra', None), ('obsolete_extra', '  '),
                                         ('unrelated', 'No source'), ('name', 'No source')])
def test_invalid_discard_cannot_clear_pending_or_change_history(client, workflow_run, field, reason):
    cid, _ = orphan(client, workflow_run)
    with client.session_factory() as db:
        before = deepcopy(db.get(WorkflowCluster, cid).payload)
    response = client.put(f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters/{cid}/golden',
        json=golden_request(client, workflow_run, selections={field: {
            'selection_mode': 'DISCARD_PENDING', 'reason': reason}}))
    assert response.status_code == 422
    with client.session_factory() as db:
        assert db.get(WorkflowCluster, cid).payload == before


def test_extra_array_source_choice_preserves_all_values():
    from types import SimpleNamespace

    from masterone.mdm.golden_service import selected_golden
    record = SimpleNamespace(id='source', external_id='source', entity_type='PERSON', quality_flags=[],
                             source_system='CRM', extra_fields={'tags': ['one', 'two']}, raw_fields={}, normalized_fields={})
    cluster = SimpleNamespace(golden={'fields': {}}, selections={'tags': {
        'selection_mode': 'SOURCE_CHOICE', 'source_record_id': 'source'}})
    db = SimpleNamespace(get=lambda *_: record)
    assert selected_golden(db, cluster)['fields']['tags']['value'] == ['one', 'two']


def test_discard_cannot_bypass_an_available_extra_source(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from masterone.mdm import golden_service
    record = SimpleNamespace(raw_fields={}, extra_fields={'extra': ['current']})
    cluster = SimpleNamespace(payload={'member_record_ids': ['source'],
        'pending_selections': {'extra': [{'value': 'old'}]}})
    monkeypatch.setattr(golden_service, 'source_records', lambda *_: [record])
    with pytest.raises(HTTPException) as error:
        golden_service.validate_selections(None, None, cluster, {'extra': {
            'selection_mode': 'DISCARD_PENDING', 'reason': 'Try bypassing review'}})
    assert error.value.status_code == 422
    assert error.value.detail['code'] == 'GOLDEN_DISCARD_INVALID'
