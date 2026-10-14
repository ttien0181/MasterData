"""Stable run-scoped identities and reversible stewardship across generations."""
from copy import deepcopy
from datetime import timedelta

import pytest
from masterone.mdm.generation_staging import digest
from masterone.mdm.golden_service import carry_choices
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowGeneration

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import finalize
from tests.test_mdm.test_workflow_golden import golden_request, ready_golden
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def group(gid, ids):
    return dict(id=gid, member_record_ids=sorted(ids), singleton=len(ids) == 1, status='DRAFT',
                golden_fields=dict(version='p119_golden_v3', fields={}, member_record_ids=sorted(ids)))


def publish_identity(db, run, sets, revision):
    from masterone.mdm.workflow_entities import assign_entity_ids
    artifact = WorkflowArtifact(run_id=run.id, phase='CLUSTER', payload={}, content_hash=digest({}))
    db.add(artifact)
    db.flush()
    generation = WorkflowGeneration(run_id=run.id, revision=revision, artifact_id=artifact.id)
    db.add(generation)
    db.flush()
    groups = [group(f'g{revision}-{i}', ids) for i, ids in enumerate(sets)]
    assigned = list(assign_entity_ids(db, run, generation, groups, actor=run.owner_user_id))
    for descriptor in assigned:
        db.add(WorkflowCluster(id=descriptor['id'], generation_id=generation.id,
                               payload=descriptor, golden=descriptor['golden_fields']))
    run.cluster_revision = revision
    db.flush()
    return assigned


def identity_state(client, workflow_run):
    ready_decisions(client, workflow_run)
    with client.session_factory() as db:
        ids = [r.id for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)]
    return ids


def test_republication_keeps_entity_ids_and_changes_generation_scoped_cluster_ids(client, workflow_run):
    first = ready_golden(client, workflow_run)
    assert all(c['entity_id'] for c in first)
    original = {tuple(c['member_record_ids']): c for c in first}
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    assert client.post(root + '/reopen-decisions', json=request(state['workflow_revision'], reason='Review again')).status_code == 200
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    second = client.get(root + '/clusters').json()['items']
    for c in second:
        old = original[tuple(c['member_record_ids'])]
        assert c['entity_id'] == old['entity_id']
        assert c['cluster_id'] != old['cluster_id']
    assert client.get(root + f"/clusters/{second[0]['cluster_id']}/golden").json()['entity_id'] == second[0]['entity_id']


def test_split_retains_anchor_branch_once_regardless_of_group_iteration_order(client, workflow_run):
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        parent = publish_identity(db, run, [ids], 1)[0]
        children = publish_identity(db, run, [[ids[2]], ids[:2]], 2)
        assert len({c['entity_id'] for c in children}) == 2
        retained = next(c for c in children if c['entity_id'] == parent['entity_id'])
        assert parent['anchor_record_id'] in retained['member_record_ids']
        assert all(parent['entity_id'] in c['ancestor_entity_ids'] for c in children)


def test_merge_selects_oldest_entity_with_stable_id_tie_break(client, workflow_run):
    from masterone.mdm.models import ResolutionEntity
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        parents = publish_identity(db, run, [ids[:2], ids[2:]], 1)
        first = db.get(ResolutionEntity, parents[0]['entity_id'])
        second = db.get(ResolutionEntity, parents[1]['entity_id'])
        assert first is not None and second is not None
        second.created_at = first.created_at - timedelta(days=1)
        merged = publish_identity(db, run, [ids], 2)[0]
        assert merged['entity_id'] == second.id
        assert set(merged['ancestor_entity_ids']) == {p['entity_id'] for p in parents}
        assert db.get(ResolutionEntity, first.id).status == 'MERGED'


def test_new_identity_is_identical_after_transaction_rollback(client, workflow_run):
    from masterone.mdm.workflow_entities import assign_entity_ids
    ids = identity_state(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        generation = WorkflowGeneration(id='retry-generation', run_id=run.id, revision=1, artifact_id='unused')
        first = list(assign_entity_ids(db, run, generation, [group('retry-group', ids)], actor=run.owner_user_id))[0]
        db.rollback()
        run = db.get(ResolutionRun, workflow_run)
        second = list(assign_entity_ids(db, run, generation, [group('retry-group', ids)], actor=run.owner_user_id))[0]
        assert second['entity_id'] == first['entity_id']


@pytest.mark.parametrize('child_members', [['a'], ['a', 'c']])
def test_changed_membership_preserves_override_as_unconfirmed_candidate(child_members):
    parent = WorkflowCluster(id='old', generation_id='old-generation',
        payload=dict(member_record_ids=['a', 'b'], entity_id='parent'), golden={'fields': {}},
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Steward value'}}, confirmed=True)
    child = WorkflowCluster(id='new', generation_id='new-generation',
        payload=dict(member_record_ids=child_members, entity_id='parent'), golden={'fields': {}}, selections={})
    carry_choices(None, type('Run', (), {'id': 'run'})(), child, [parent])
    assert child.payload['pending_selections']['name'][0]['value'] == 'Steward value'
    assert child.payload['requires_manual_review'] == ['name']
    assert child.confirmed is False


def test_other_split_branch_has_candidate_without_applying_parent_override():
    parent = WorkflowCluster(id='old', generation_id='old-generation',
        payload=dict(member_record_ids=['a', 'b'], entity_id='parent'), golden={'fields': {}},
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Steward value'}}, confirmed=True)
    child = WorkflowCluster(id='new', generation_id='new-generation',
        payload=dict(member_record_ids=['b'], entity_id='new-entity'), golden={'fields': {}}, selections={})
    carry_choices(None, type('Run', (), {'id': 'run'})(), child, [parent])
    assert child.payload['pending_selections']['name'][0]['value'] == 'Steward value'
    assert 'name' not in child.selections


def test_colliding_ancestor_values_remain_visible_as_separate_candidates():
    parents = [WorkflowCluster(id=f'parent-{i}', generation_id='old',
        payload=dict(member_record_ids=[rid], entity_id=f'entity-{i}'), golden={'fields': {}},
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': name}}, confirmed=True)
        for i, (rid, name) in enumerate([('a', 'First override'), ('b', 'Second override')])]
    child = WorkflowCluster(id='child', generation_id='new', payload=dict(member_record_ids=['a', 'b'], entity_id='entity-0'),
                            golden={'fields': {}}, selections={})
    carry_choices(None, type('Run', (), {'id': 'run'})(), child, parents)
    assert {v['value'] for v in child.payload['pending_selections']['name']} == {'First override', 'Second override'}
    assert 'name' not in child.selections


def test_pending_choice_survives_another_generation_without_a_selection():
    pending = {'name': [{'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Unconfirmed', 'ancestor_cluster_id': 'earlier'}]}
    parent = WorkflowCluster(id='old', generation_id='old-generation',
        payload=dict(member_record_ids=['a'], entity_id='parent', pending_selections=pending, requires_manual_review=['name']),
        golden={'fields': {}}, selections={}, confirmed=False)
    child = WorkflowCluster(id='new', generation_id='new-generation',
        payload=dict(member_record_ids=['a'], entity_id='parent'), golden={'fields': {}}, selections={})
    before = deepcopy(parent.payload)
    carry_choices(None, type('Run', (), {'id': 'run'})(), child, [parent])
    assert child.payload['pending_selections'] == pending
    assert parent.payload == before


def test_new_export_identity_contract_uses_entity_ids(client, workflow_run):
    from masterone.mdm.workflow_exports import master_record_id
    assert master_record_id({'id': 'historic-cluster'}, {}) == 'historic-cluster'
    assert master_record_id({'id': 'new-cluster', 'entity_id': 'stable'}, {'master_id_version': 'workflow_entity_v1'}) == 'stable'
    with pytest.raises(ValueError, match='WORKFLOW_ENTITY_IDENTITY_INVALID'):
        master_record_id({'id': 'new-cluster'}, {'master_id_version': 'workflow_entity_v1'})


def test_explicitly_saved_choice_clears_only_its_pending_candidates(client, workflow_run):
    clusters = ready_golden(client, workflow_run)
    cid = next(c['cluster_id'] for c in clusters if c['member_count'] > 1)
    with client.session_factory.begin() as db:
        row = db.get(WorkflowCluster, cid)
        row.payload = {**row.payload, 'pending_selections': {
            'name': [{'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Old name'}],
            'phone': [{'selection_mode': 'MANUAL_OVERRIDE', 'value': '0912345678'}]},
            'requires_manual_review': ['name', 'phone']}
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    payload = golden_request(client, workflow_run, selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Chosen'}})
    saved = client.put(root + f'/clusters/{cid}/golden', json=payload)
    assert saved.status_code == 200, saved.text
    current = client.get(root + f'/clusters/{cid}/golden').json()
    assert set(current['pending_selections']) == {'phone'}
    response = client.post(root + f'/clusters/{cid}/golden/confirm', json=golden_request(client, workflow_run))
    assert response.status_code == 409


@pytest.mark.parametrize('change', ['value', 'quality', 'timestamp'])
def test_source_choice_survives_only_an_unchanged_pinned_observation(client, workflow_run, change):
    clusters = ready_golden(client, workflow_run)
    descriptor = next(c for c in clusters if c['member_count'] > 1)
    cid, rid = descriptor['cluster_id'], descriptor['member_record_ids'][0]
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.put(root + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': rid}}))
    assert response.status_code == 200, response.text
    with client.session_factory.begin() as db:
        run, parent = db.get(ResolutionRun, workflow_run), db.get(WorkflowCluster, cid)
        child = WorkflowCluster(id='child', generation_id='next', payload=deepcopy(parent.payload), golden=parent.golden, selections={})
        carry_choices(db, run, child, [parent])
        assert child.selections['name']['source_record_id'] == rid
        source = db.get(ResolutionRecord, rid)
        old_value = parent.selections['name']['value']
        if change == 'value':
            source.raw_fields = {**source.raw_fields, 'name': 'Updated source name'}
        elif change == 'quality':
            source.normalized_fields = {**source.normalized_fields, '_field_quality': {
                **source.normalized_fields.get('_field_quality', {}), 'name': 'INVALID'}}
        else:
            source.normalized_fields = {**source.normalized_fields, '_source_updated_at': '2026-10-14T00:00:00+07:00'}
        fresh = WorkflowCluster(id='fresh', generation_id='next', payload=deepcopy(parent.payload), golden=parent.golden, selections={})
        carry_choices(db, run, fresh, [parent])
        assert 'name' not in fresh.selections
        assert fresh.payload['pending_selections']['name'][0]['value'] == old_value
        assert fresh.confirmed is False


def test_entity_history_records_the_actual_selected_golden(client, workflow_run):
    from masterone.mdm.models import ResolutionEntityVersion
    clusters = ready_golden(client, workflow_run)
    descriptor = next(c for c in clusters if c['member_count'] > 1)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.put(root + f"/clusters/{descriptor['cluster_id']}/golden", json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Selected history name'}}))
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        latest = db.query(ResolutionEntityVersion).filter_by(entity_id=descriptor['entity_id']).order_by(
            ResolutionEntityVersion.version.desc()).first()
        assert latest.golden_fields['fields']['name']['value'] == 'Selected history name'
        assert latest.changed_by == db.get(ResolutionRun, workflow_run).owner_user_id


@pytest.mark.parametrize('parquet', [False, True])
def test_republication_history_keeps_inherited_selected_golden_and_choice_metadata(client, workflow_run, monkeypatch, parquet):
    from masterone.config import get_settings
    monkeypatch.setattr(get_settings(), 'workflow_parquet_enabled', parquet)
    from masterone.mdm.models import ResolutionEntityVersion
    groups = ready_golden(client, workflow_run)
    descriptor = next(g for g in groups if g['member_count'] > 1)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.put(root + f"/clusters/{descriptor['cluster_id']}/golden", json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Inherited chosen name'}}))
    assert response.status_code == 200
    with client.session_factory() as db:
        history = {v.id: deepcopy(v.golden_fields) for v in db.query(ResolutionEntityVersion)}
    state = read(client, workflow_run)
    assert client.post(root + '/reopen-decisions', json=request(state['workflow_revision'], reason='Review again')).status_code == 200
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    with client.session_factory() as db:
        latest = db.query(ResolutionEntityVersion).filter_by(entity_id=descriptor['entity_id']).order_by(
            ResolutionEntityVersion.version.desc()).first()
        assert latest.golden_fields['fields']['name']['value'] == 'Inherited chosen name'
        assert latest.golden_fields['_workflow_choices']['selections']['name']['value'] == 'Inherited chosen name'
        assert all(db.get(ResolutionEntityVersion, key).golden_fields == value for key,value in history.items())


@pytest.mark.parametrize('parquet', [False, True])
def test_implicit_singletons_are_ancestors_when_they_later_merge(client, workflow_run, monkeypatch, parquet):
    from masterone.config import get_settings
    from masterone.mdm.workflow_entities import plan_entity_ids, singleton_entity_id
    from masterone.mdm.workflow_models import WorkflowPair
    monkeypatch.setattr(get_settings(), 'workflow_parquet_enabled', parquet)
    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        for pair in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            pair.decision = 'REJECTED'
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        ids = [r.id for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)][:2]
        before = {singleton_entity_id(run.id, rid) for rid in ids}
        generation = WorkflowGeneration(id='next-gen', run_id=run.id, revision=2, artifact_id='unused')
        merged = plan_entity_ids(db, run, generation, [group('merged', ids)])[0]
        assert set(merged['ancestor_entity_ids']) == before
        assert merged['entity_id'] == min(before)


def test_split_without_retained_anchor_uses_overlap_then_smallest_member(client, workflow_run):
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        parent = publish_identity(db, run, [ids], 1)[0]
        children = publish_identity(db, run, [[ids[2]], [ids[1]]], 2)
        assert next(g for g in children if g['entity_id'] == parent['entity_id'])['member_record_ids'] == [ids[1]]


def test_run_scoped_singleton_identity_does_not_link_independent_runs():
    from masterone.mdm.workflow_entities import singleton_entity_id
    assert singleton_entity_id('run-a', 'source') == singleton_entity_id('run-a', 'source')
    assert singleton_entity_id('run-a', 'source') != singleton_entity_id('run-b', 'source')


def test_public_source_choices_can_round_trip_without_private_hash_fields(client, workflow_run):
    clusters = ready_golden(client, workflow_run)
    descriptor = next(c for c in clusters if c['member_count'] > 1)
    cid = descriptor['cluster_id']
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.put(root + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': descriptor['member_record_ids'][0]}}))
    assert response.status_code == 200
    current = client.get(root + f'/clusters/{cid}/golden').json()
    assert 'source_choice_hash' not in current['selections']['name']
    response = client.put(root + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections=current['selections']))
    assert response.status_code == 200, response.text


def test_preview_pins_stable_entity_ids_without_publishing_entities(client, workflow_run):
    from masterone.mdm.models import ResolutionEntity

    from tests.test_mdm.test_workflow_finalize import complete_reviews
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    response = client.post(root + '/cluster-preview', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202
    run_one(client)
    preview = client.get(root + '/operations/' + response.json()['operation_id']).json()
    planned = {tuple(g['member_record_ids']): g['entity_id'] for g in preview['result']['groups']}
    with client.session_factory() as db:
        assert db.query(ResolutionEntity).filter_by(run_id=workflow_run).count() == 0
    state = read(client, workflow_run)
    response = client.post(root + '/finalize-decisions', json=request(state['workflow_revision'],
        expected_decision_revision=state['decision_revision'], preview_operation_id=preview['operation_id'],
        preview_token=preview['result']['preview_token']))
    assert response.status_code == 202
    run_one(client)
    actual = client.get(root + '/clusters').json()['items']
    assert {tuple(g['member_record_ids']): g['entity_id'] for g in actual} == planned


def test_equal_age_merge_uses_entity_id_and_preserves_immutable_history(client, workflow_run):
    from masterone.mdm.models import ResolutionEntity, ResolutionEntityVersion
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        parents = publish_identity(db, run, [ids[:2], ids[2:]], 1)
        entities = [db.get(ResolutionEntity, p['entity_id']) for p in parents]
        entities[1].created_at = entities[0].created_at
        history = {v.id: deepcopy(v.golden_fields) for v in db.query(ResolutionEntityVersion)}
        merged = publish_identity(db, run, [ids], 2)[0]
        assert merged['entity_id'] == min(p['entity_id'] for p in parents)
        for version_id, fields in history.items():
            assert db.get(ResolutionEntityVersion, version_id).golden_fields == fields


def test_csv_uses_stable_entity_identity_and_declares_version(client, workflow_run):
    import csv
    from io import StringIO
    clusters = ready_golden(client, workflow_run)
    expected = {frozenset(c['member_record_ids']): c['entity_id'] for c in clusters}
    base = f'/api/v1/mdm/runs/{workflow_run}'
    root = base + '/workflow'
    for group_row in clusters:
        assert client.post(root + f"/clusters/{group_row['cluster_id']}/golden/confirm",
                           json=golden_request(client, workflow_run)).status_code == 200
    state = read(client, workflow_run)
    response = client.post(root + '/finalize-golden', json=request(state['workflow_revision'],
        expected_golden_revision=state['golden_revision']))
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=state['cluster_revision']).one()
        assert generation.finalized_result['master_id_version'] == 'workflow_entity_v1'
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    response = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute',
                           json={'execute_request_id': 'stable-identity-export'})
    assert response.status_code == 200, response.text
    actual = {frozenset(row['source_record_ids'].split(';')): row['master_record_id']
              for row in csv.DictReader(StringIO(response.content.decode('utf-8-sig')))}
    assert actual == expected


def test_bulk_confirmation_cannot_acknowledge_pending_values(client, workflow_run):
    clusters = ready_golden(client, workflow_run)
    cid = clusters[0]['cluster_id']
    with client.session_factory.begin() as db:
        row = db.get(WorkflowCluster, cid)
        row.payload = {**row.payload, 'pending_selections': {
            'name': [{'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Unreviewed'}]}, 'requires_manual_review': ['name']}
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run, cluster_ids=[cid]))
    assert response.status_code == 409, response.text
    with client.session_factory() as db:
        assert db.get(WorkflowCluster, cid).confirmed is False


def test_repair_cannot_preserve_confirmation_when_source_changes_but_golden_does_not():
    from masterone.mdm.golden_service import repair_confirmation
    from masterone.mdm.pipeline.golden import build_golden

    from tests.test_mdm.test_pair_classification_regressions import record
    before = record('a')
    before['normalized_fields']['_source_updated_at'] = '2026-10-13T10:00:00'
    after = deepcopy(before)
    after['normalized_fields']['_source_updated_at'] = '2026-10-14T10:00:00'
    old_golden = build_golden(['a'], {'a': before})
    new_golden = build_golden(['a'], {'a': after})
    assert old_golden == new_golden  # Invalid source times are not survivorship clocks.
    parent = WorkflowCluster(id='old', generation_id='old', payload=dict(member_record_ids=['a'],
        source_snapshot_hash=digest([before])), golden=old_golden, selections={}, confirmed=True)
    child = WorkflowCluster(id='new', generation_id='new', payload=dict(member_record_ids=['a'],
        source_snapshot_hash=digest([after])), golden=new_golden, selections={}, confirmed=False)
    assert repair_confirmation(child, [parent], {'repair_record_ids': ['other']}) is False
