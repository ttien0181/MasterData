import pytest
from masterone.mdm.workflow_artifacts import load, seal
from masterone.mdm.workflow_models import WorkflowArtifact
from sqlalchemy import update

from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_stages import ready_decisions, run_one


def test_large_snapshot_is_partitioned_and_round_trips(client, workflow_run):
    payload = {'records': [{'id': str(i), 'name': 'An'} for i in range(1100)],
               'pairs': [], 'workflow_context': {'decision_revision': 3}}
    artifact_id = seal(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', payload)
    with client.session_factory() as db:
        row = db.get(WorkflowArtifact, artifact_id)
        assert row.payload.get('__workflow_parts__'), 'Large arrays must not occupy one JSON value'
        assert len(row.payload['records']) == 0
        assert row.payload['workflow_context'] == payload['workflow_context']
        assert load(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') == payload


@pytest.mark.parametrize('fault', ['root_hash', 'part_hash', 'part_owner', 'part_count'])
def test_legacy_list_iterator_keeps_partition_integrity_checks(client, workflow_run, fault):
    from masterone.mdm import workflow_artifacts
    from masterone.mdm.generation_staging import digest
    payload = {'records': [{'id': str(i)} for i in range(1100)]}
    artifact_id = seal(client.session_factory, workflow_run, 'SINGLETON_SOURCE', payload)
    with client.session_factory.begin() as db:
        root = db.get(WorkflowArtifact, artifact_id)
        descriptor = root.payload['__workflow_parts__'][0]
        part = db.get(WorkflowArtifact, descriptor['parts'][0]['id'])
        if fault == 'root_hash':
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == root.id).values(content_hash='changed'))
        elif fault == 'part_hash':
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == part.id).values(content_hash='changed'))
        elif fault == 'part_owner':
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == part.id).values(run_id='another-run'))
        else:
            changed_part = {'items': []}
            changed_hash = digest(changed_part)
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == part.id).values(
                payload=changed_part, content_hash=changed_hash))
            changed = {**root.payload, '__workflow_parts__': [
                {**descriptor, 'parts': [{**descriptor['parts'][0], 'hash': changed_hash}, *descriptor['parts'][1:]]}]}
            db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == root.id).values(
                payload=changed, content_hash=digest(changed)))
    reader = getattr(workflow_artifacts, 'iter_list', None)
    assert callable(reader), 'Legacy arrays need a verified partition iterator'
    with client.session_factory() as db, pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_INVALID'):
        list(reader(db, artifact_id, workflow_run, 'SINGLETON_SOURCE', ('records',)))


def test_partitioned_snapshot_is_found_by_logical_hash_for_golden_resolution(client, workflow_run):
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.workflow_resolution import resolution_snapshot
    payload = {'records': [{'id': str(i)} for i in range(1100)], 'decision_revision': 3}
    seal(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', payload)
    with client.session_factory() as db:
        found = resolution_snapshot(db, workflow_run, {'workflow_context': {'snapshot_hash': digest(payload)}})
    assert found == payload


def test_nested_plan_parts_are_verified_before_use(client, workflow_run):
    payload = {'computed': {'clusters': [{'member_record_ids': [str(i)]} for i in range(1100)]}}
    artifact_id = seal(client.session_factory, workflow_run, 'CLUSTER_PLAN', payload)
    with client.session_factory.begin() as db:
        root = db.get(WorkflowArtifact, artifact_id)
        assert root.payload.get('__workflow_parts__'), 'Complete plans need bounded storage too'
        part_id = root.payload['__workflow_parts__'][0]['parts'][0]['id']
        part = db.get(WorkflowArtifact, part_id)
        db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == part_id).values(
            payload={**part.payload, 'items': []}))
    with client.session_factory() as db, pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_INVALID'):
        load(db, artifact_id, workflow_run, 'CLUSTER_PLAN')


def test_new_group_detail_reads_only_local_evidence(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    group = next(c for c in client.get(url + '/clusters').json()['items'] if c['member_count'] > 1)
    monkeypatch.setattr('masterone.mdm.workflow_artifacts.load', lambda *_: (_ for _ in ()).throw(
        AssertionError('Opening a group must not load the entire generation artifact')))
    response = client.get(url + '/clusters/' + group['cluster_id'])
    assert response.status_code == 200
    detail = response.json()
    assert detail['members']['total'] == 2
    assert detail['pair_evidence']['total'] == 1
    assert detail['workflow_context']['decision_revision'] >= 0


def test_partitioned_group_page_loads_only_intersecting_parts(client, workflow_run):
    from masterone.mdm import workflow_artifacts
    payload = {'computed': {'clusters': [{'id': str(i)} for i in range(1600)]}}
    artifact_id = seal(client.session_factory, workflow_run, 'CLUSTER_PLAN', payload)
    reader = getattr(workflow_artifacts, 'list_page', None)
    assert callable(reader), 'Large plans need bounded page reads'
    with client.session_factory() as db:
        page = reader(db, artifact_id, workflow_run, 'CLUSTER_PLAN', ('computed', 'clusters'), 510, 5)
    assert [row['id'] for row in page['items']] == ['510', '511', '512', '513', '514']
    assert page['total'] == 1600 and page['next_cursor'] == '515'


def test_large_decision_maps_are_partitioned_too(client, workflow_run):
    payload = {'overlays': {str(i): 'ACCEPTED' for i in range(1100)}, 'records': []}
    artifact_id = seal(client.session_factory, workflow_run, 'DECISION_SNAPSHOT', payload)
    with client.session_factory() as db:
        assert db.get(WorkflowArtifact, artifact_id).payload['overlays'] == {}
        assert load(db, artifact_id, workflow_run, 'DECISION_SNAPSHOT') == payload
