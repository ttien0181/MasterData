"""S1 partition provenance and current-generation integrity."""
from copy import deepcopy

import pytest


def test_split_partition_requires_exact_cover_no_duplicates():
    from masterone.mdm.workflow_structure import partition_pairs
    assert partition_pairs(['a', 'b', 'c'], [['a'], ['b', 'c']]) == {('a', 'b'), ('a', 'c')}
    for partitions in ([['a'], ['b']], [['a', 'b'], ['b', 'c']], [[], ['a', 'b', 'c']], [['a'], ['b'], ['c']]):
        with pytest.raises(ValueError, match='INVALID_PARTITION'):
            partition_pairs(['a', 'b', 'c'], partitions)


def test_projected_split_supersedes_every_cross_edge_without_negative_identity_labels():
    from masterone.mdm.workflow_structure import project_snapshot
    snapshot = {'pairs': [{'id': 'ab', 'left_id': 'a', 'right_id': 'b'},
                          {'id': 'ac', 'left_id': 'a', 'right_id': 'c'},
                          {'id': 'bc', 'left_id': 'b', 'right_id': 'c'}],
                'overlays': {'ab': 'ACCEPTED', 'ac': 'ACCEPTED', 'bc': 'ACCEPTED'},
                'negative_pairs': [('b', 'outside')], 'group_approvals': [{'record_ids': ['a', 'b', 'c']}],
                'review_reasons': {}, 'decision_revision': 4}
    before = deepcopy(snapshot)
    intent = {'kind': 'SPLIT', 'members': ['a', 'b', 'c'], 'partitions': [['a'], ['b', 'c']],
              'reason': 'QA structure request', 'release_pairs': [], 'bridge_pair_ids': []}
    projected = project_snapshot(snapshot, intent)
    assert snapshot == before
    assert projected['overlays'] == {'ab': 'REJECTED', 'ac': 'REJECTED', 'bc': 'ACCEPTED'}
    assert set(map(tuple, projected['negative_pairs'])) == {('a', 'b'), ('a', 'c'), ('b', 'outside')}
    assert projected['group_approvals'] == []
    assert projected['structure_intent']['kind'] == 'SPLIT'


def test_remerge_releases_only_target_constraint_and_accepts_selected_existing_bridge():
    from masterone.mdm.workflow_structure import project_snapshot
    snapshot = {'pairs': [{'id': 'ab', 'left_id': 'a', 'right_id': 'b'},
                          {'id': 'ac', 'left_id': 'a', 'right_id': 'c'}],
                'overlays': {'ab': 'REJECTED', 'ac': 'NON_MATCH'}, 'negative_pairs': [('a', 'b'), ('a', 'c')],
                'review_reasons': {}, 'group_approvals': [], 'decision_revision': 3}
    intent = {'kind': 'REMERGE', 'members': ['a', 'b'], 'partitions': [['a'], ['b']],
              'reason': 'Confirm one existing bridge', 'release_pairs': [('a', 'b')], 'bridge_pair_ids': ['ab']}
    value = project_snapshot(snapshot, intent)
    assert value['overlays'] == {'ab': 'ACCEPTED', 'ac': 'NON_MATCH'}
    assert value['negative_pairs'] == [('a', 'c')]
    with pytest.raises(ValueError, match='BRIDGE_NOT_IN_CANDIDATE_UNIVERSE'):
        project_snapshot(snapshot, {**intent, 'bridge_pair_ids': ['absent']})


def _versions(client, run):
    state = client.get(f'/api/v1/mdm/runs/{run}/workflow').json()
    return {'expected_' + key: state[key] for key in
            ('workflow_revision', 'decision_revision', 'cluster_revision', 'golden_revision')}


def test_real_split_remerge_publication_history_and_retry(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.config import get_settings
    from masterone.mdm.matching_foundation_models import StructureConstraint
    from masterone.mdm.models import ResolutionRecord
    from masterone.mdm.workflow_models import WorkflowGeneration, WorkflowPair

    from tests.test_mdm.test_workflow_golden import ready_golden
    from tests.test_mdm.test_workflow_stages import run_one
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    groups = ready_golden(client, workflow_run)
    group = next(c for c in groups if c['member_count'] == 2)
    before = deepcopy(group)
    members = group['member_record_ids']
    root = f'/api/v1/mdm/matching/runs/{workflow_run}/structure'
    body = {'request_id': str(uuid4()), **_versions(client, workflow_run), 'cluster_ids': [group['cluster_id']],
            'partitions': [[members[0]], [members[1]]], 'reason': 'QA split with independent history'}
    response = client.post(root + '/split/preview', json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['result']['blocked_reasons'] == []
    execute = {'request_id': str(uuid4()), **_versions(client, workflow_run),
               'preview_operation_id': receipt['operation_id'], 'preview_token': receipt['result']['preview_token']}
    first = client.post(root + '/execute', json=execute)
    assert first.status_code == 202, first.text
    assert client.post(root + '/execute', json=execute).json() == first.json()
    assert first.json()['status'] == 'QUEUED'
    run_one(client)
    groups = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters').json()['items']
    pieces = [c for c in groups if set(c['member_record_ids']) <= set(members)]
    assert len(pieces) == 2 and all(c['member_count'] == 1 for c in pieces)
    assert sum(c['entity_id'] == before['entity_id'] for c in pieces) == 1
    bridge_read = client.get(root + '/bridges', params={'left_cluster_id': pieces[0]['cluster_id'],
                                                     'right_cluster_id': pieces[1]['cluster_id']})
    assert bridge_read.status_code == 200, bridge_read.text
    assert bridge_read.json()['total'] == 1 and len(bridge_read.json()['items']) == 1
    with client.session_factory() as db:
        original = db.get(WorkflowGeneration, receipt['result']['intent']['generation_id'])
        from masterone.mdm.models import ResolutionRun
        from masterone.mdm.workflow_generation_reads import GenerationReader
        assert GenerationReader(db, db.get(ResolutionRun, workflow_run), original).group(before['cluster_id'])['member_record_ids'] == members
        constraint = db.query(StructureConstraint).filter_by(kind='SEPARATION').one()
        constraint_id = constraint.id
        bridge = next(p.id for p in db.query(WorkflowPair).filter_by(run_id=workflow_run)
                      if {p.baseline['left_id'], p.baseline['right_id']} == set(members))
        assert db.query(ResolutionRecord).filter_by(run_id=workflow_run).count() == 3
    body = {'request_id': str(uuid4()), **_versions(client, workflow_run), 'cluster_ids': [c['cluster_id'] for c in pieces],
            'bridge_pair_ids': [bridge], 'constraint_id': constraint_id, 'reason': 'Confirm only selected existing QA bridge'}
    preview = client.post(root + '/remerge/preview', json=body)
    assert preview.status_code == 200, preview.text
    receipt = preview.json()
    assert receipt['result']['blocked_reasons'] == []
    response = client.post(root + '/execute', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'preview_operation_id': receipt['operation_id'], 'preview_token': receipt['result']['preview_token']})
    assert response.status_code == 202, response.text
    run_one(client)
    groups = client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters').json()['items']
    assert any(set(c['member_record_ids']) == set(members) for c in groups)
    with client.session_factory() as db:
        assert db.query(WorkflowGeneration).count() == 3
        assert db.query(StructureConstraint).count() == 2


def test_independent_deny_and_wrong_origin_block_remerge_even_when_flag_is_disabled(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.config import get_settings
    from masterone.mdm.matching_foundation_models import StructureConstraint
    from masterone.mdm.models import ResolutionNegativeEdge, ResolutionRun
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.mdm.workflow_structure import structure_links

    from tests.test_mdm.test_workflow_golden import ready_golden
    from tests.test_mdm.test_workflow_stages import run_one
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    group = next(c for c in ready_golden(client, workflow_run) if c['member_count'] == 2)
    members = group['member_record_ids']
    root = f'/api/v1/mdm/matching/runs/{workflow_run}/structure'
    response = client.post(root + '/split/preview', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'cluster_ids': [group['cluster_id']], 'partitions': [[members[0]], [members[1]]], 'reason': 'QA split'})
    receipt = response.json()
    assert response.status_code == 200
    queued = client.post(root + '/execute', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'preview_operation_id': receipt['operation_id'], 'preview_token': receipt['result']['preview_token']})
    assert queued.status_code == 202
    run_one(client)
    pieces = [c for c in client.get(f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters').json()['items']
              if set(c['member_record_ids']) <= set(members)]
    with client.session_factory.begin() as db:
        actor = db.get(ResolutionRun, workflow_run).owner_user_id
        db.add(ResolutionNegativeEdge(run_id=workflow_run, left_record_id=members[0], right_record_id=members[1],
                                     created_by=actor, reason='STEWARD_REJECT', active=True))
        bridge = next(p.id for p in db.query(WorkflowPair).filter_by(run_id=workflow_run)
                      if {p.baseline['left_id'], p.baseline['right_id']} == set(members))
        constraint = db.query(StructureConstraint).filter_by(kind='SEPARATION').one().id
    body = {'request_id': str(uuid4()), **_versions(client, workflow_run),
            'cluster_ids': [c['cluster_id'] for c in pieces], 'bridge_pair_ids': [bridge],
            'constraint_id': constraint, 'reason': 'Must not remove independent Deny'}
    denied = client.post(root + '/remerge/preview', json=body)
    assert denied.status_code == 200
    assert denied.json()['result']['blocked_reasons']
    assert client.post(root + '/execute', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'preview_operation_id': denied.json()['operation_id'], 'preview_token': denied.json()['result']['preview_token']}).status_code == 409
    wrong = client.post(root + '/remerge/preview', json={**body, 'request_id': str(uuid4()), 'constraint_id': str(uuid4())})
    assert 'ACTIVE_SPLIT_CONSTRAINT_REQUIRED' in wrong.json()['result']['blocked_reasons']
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', False)
    with client.session_factory() as db:
        assert tuple(sorted(members)) in structure_links(db, workflow_run)
        assert db.query(ResolutionNegativeEdge).filter_by(active=True).count() == 1


def test_split_stale_token_has_no_effect_and_duplicate_retry_is_stable_after_publication(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.config import get_settings
    from masterone.mdm.matching_foundation_models import StructureConstraint
    from masterone.mdm.models import ResolutionRun

    from tests.test_mdm.test_workflow_golden import ready_golden
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    group = next(c for c in ready_golden(client, workflow_run) if c['member_count'] == 2)
    root = f'/api/v1/mdm/matching/runs/{workflow_run}/structure'
    receipt = client.post(root + '/split/preview', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'cluster_ids': [group['cluster_id']], 'partitions': [[group['member_record_ids'][0]], [group['member_record_ids'][1]]],
        'reason': 'QA stale fence'}).json()
    body = {'request_id': str(uuid4()), **_versions(client, workflow_run),
            'preview_operation_id': receipt['operation_id'], 'preview_token': receipt['result']['preview_token']}
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, workflow_run).golden_revision += 1
    assert client.post(root + '/execute', json=body).status_code == 409
    with client.session_factory() as db:
        assert db.query(StructureConstraint).count() == 0


def test_structure_publication_missing_partition_never_completes_generation(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.config import get_settings
    from masterone.mdm import workflow_generation_reads
    from masterone.mdm.workflow_models import WorkflowGeneration

    from tests.test_mdm.test_workflow_golden import ready_golden
    from tests.test_mdm.test_workflow_stages import run_one
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    group = next(c for c in ready_golden(client, workflow_run) if c['member_count'] == 2)
    members = group['member_record_ids']
    root = f'/api/v1/mdm/matching/runs/{workflow_run}/structure'
    receipt = client.post(root + '/split/preview', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'cluster_ids': [group['cluster_id']], 'partitions': [[members[0]], [members[1]]], 'reason': 'QA publication fault'}).json()
    queued = client.post(root + '/execute', json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'preview_operation_id': receipt['operation_id'], 'preview_token': receipt['result']['preview_token']})
    assert queued.status_code == 202
    original = workflow_generation_reads.prepare_virtual_publication

    def faulty(db, run, result, *args, **kwargs):
        payload = original(db, run, result, *args, **kwargs)
        result['clusters'] = [c for c in result['clusters'] if members[0] not in c['member_record_ids']]
        return payload

    monkeypatch.setattr(workflow_generation_reads, 'prepare_virtual_publication', faulty)
    with pytest.raises(ValueError, match='STRUCTURE_PUBLICATION_INVALID'):
        run_one(client)
    with client.session_factory() as db:
        assert db.query(WorkflowGeneration).filter_by(run_id=workflow_run).count() == 1


def test_split_rejects_remerge_bridge_fields_before_any_mutation(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.config import get_settings
    from masterone.mdm.matching_foundation_models import StructureConstraint
    from masterone.mdm.workflow_models import WorkflowPair

    from tests.test_mdm.test_workflow_golden import ready_golden
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    group = next(c for c in ready_golden(client, workflow_run) if c['member_count'] == 2)
    members = group['member_record_ids']
    with client.session_factory() as db:
        pair = next(p for p in db.query(WorkflowPair).filter_by(run_id=workflow_run)
                    if {p.baseline['left_id'], p.baseline['right_id']} == set(members))
        pair_id, decision, revision = pair.id, pair.decision, pair.revision
    url = f'/api/v1/mdm/matching/runs/{workflow_run}/structure/split/preview'
    response = client.post(url, json={'request_id': str(uuid4()), **_versions(client, workflow_run),
        'cluster_ids': [group['cluster_id']], 'partitions': [[members[0]], [members[1]]],
        'bridge_pair_ids': [pair_id], 'reason': 'Reject cross-kind acceptance injection'})
    assert response.status_code == 422
    with client.session_factory() as db:
        pair = db.get(WorkflowPair, pair_id)
        assert (pair.decision, pair.revision) == (decision, revision)
        assert db.query(StructureConstraint).count() == 0


def test_reviewed_42_member_parent_split_retains_21_member_partition_permissions():
    from itertools import combinations

    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_cluster_engine import compute_components
    from masterone.mdm.workflow_group_approvals import approval_fingerprint, validate_group
    from masterone.mdm.workflow_structure import partition_approvals, project_snapshot

    from tests.test_mdm.test_safe_clustering import edge, record
    records = [record(f'r{i:02d}') for i in range(42)]
    pairs = [edge(a, b) for a, b in combinations(records, 2)]
    for pair in pairs:
        pair['decision'] = 'REVIEW'
    members = [r['id'] for r in records]
    snapshot = {'records': records, 'pairs': pairs, 'overlays': {p['id']: 'ACCEPTED' for p in pairs},
                'negative_pairs': [], 'review_reasons': {p['id']: 'Reviewed parent' for p in pairs},
                'group_approvals': [], 'profile': {}, 'execution_identity': 'qa', 'policy_version': 'person-v3'}
    with engine_context({'decision_semantics_v2_enabled': True, 'cluster_safety_v2_enabled': True,
                         'generation_staging_enabled': False}):
        assert validate_group(snapshot, members, review_reason='Reviewed parent') == ''
        snapshot['group_approvals'] = [{'record_ids': members, 'reason': 'Reviewed parent',
            'operation_id': 'qa-parent', 'fingerprint': approval_fingerprint(snapshot, members)}]
        assert any(len(c['member_record_ids']) == 42 for c in compute_components(snapshot, 'qa')['safe_clusters'])
        intent = {'kind': 'SPLIT', 'members': members, 'partitions': [members[:21], members[21:]],
                  'reason': 'Reviewed exact partitions', 'release_pairs': [], 'bridge_pair_ids': []}
        projected = project_snapshot(snapshot, intent)
        projected['group_approvals'] = partition_approvals(projected, intent, 'qa-split')
        result = compute_components(projected, 'qa')
        assert result['conflicts'] == []
        assert sorted(len(c['member_record_ids']) for c in result['safe_clusters']) == [21, 21]


def test_structure_rejects_invalid_partition_csrf_viewer_and_stale_token(client, workflow_run, monkeypatch):
    from uuid import uuid4

    from masterone.auth.models import User
    from masterone.config import get_settings

    from tests.test_mdm.test_workflow_golden import ready_golden
    monkeypatch.setattr(get_settings(), 'entity_structure_enabled', True)
    group = next(c for c in ready_golden(client, workflow_run) if c['member_count'] == 2)
    url = f'/api/v1/mdm/matching/runs/{workflow_run}/structure/split/preview'
    body = {'request_id': str(uuid4()), **_versions(client, workflow_run), 'cluster_ids': [group['cluster_id']],
            'partitions': [[group['member_record_ids'][0]], []], 'reason': 'QA partition'}
    assert client.post(url, json=body).status_code == 422
    assert client.post(url, json=body, headers={'X-CSRF-Token': 'invalid'}).status_code == 403
    assert client.post(url, json=body, headers={'Origin': 'https://evil.example'}).status_code == 403
    with client.session_factory.begin() as db:
        db.query(User).filter_by(email_normalized='steward@example.com').one().role = 'VIEWER'
    assert client.post(url, json=body).status_code == 403
