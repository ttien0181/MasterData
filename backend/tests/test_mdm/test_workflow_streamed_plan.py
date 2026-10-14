"""Disk plans preserve guarded publication and full unresolved regions."""
from copy import deepcopy

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.workflow_snapshot_storage import open_snapshot, seal_snapshot

from tests.test_mdm.test_pair_classification_regressions import record
from tests.test_mdm.test_safe_clustering import edge
from tests.test_mdm.test_workflow_snapshot_streaming import operation


def freeze(client, run_id, source):
    streams = {key: source[key] for key in ('records', 'pairs')}
    for key in ('overlays', 'review_reasons'):
        streams[key] = ({'id': pid, 'value': value} for pid, value in source.get(key, {}).items())
    streams['negative_pairs'] = ({'left_id': a, 'right_id': b} for a, b in source.get('negative_pairs', []))
    streams['known_conflicts'] = source.get('known_conflicts', [])
    streams['group_approvals'] = source.get('group_approvals', [])
    metadata = {k: v for k, v in source.items() if k not in streams}
    return seal_snapshot(client.session_factory, run_id, 'DECISION_SNAPSHOT', metadata, streams,
                         operation_id=operation(client, run_id))


@pytest.mark.parametrize('mode', ['accepted', 'pending', 'weak', 'negative', 'known-missing'])
def test_streamed_plan_matches_guarded_publication(client, workflow_run, mode):
    from masterone.mdm.workflow_finalize import compute_publication
    from masterone.mdm.workflow_streamed_plan import StreamedPlan
    records = [record(rid) for rid in ['a', 'b', 'c', 'x']]
    pairs = [edge(records[0], records[1]), edge(records[1], records[2])]
    source = dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
        review_reasons={}, negative_pairs=[], allow_partial=True, known_conflicts=[], group_approvals=[],
        policy_version='person-v3', profile={'cluster_safety_v2_enabled': True, 'decision_semantics_v2_enabled': True})
    if mode == 'pending':
        source['overlays'][pairs[1]['id']] = 'UNDECIDED'
        pairs[1]['decision'] = 'REVIEW'
    elif mode == 'weak':
        source['overlays'][pairs[1]['id']] = 'UNDECIDED'
        pairs[1]['decision'] = 'INSUFFICIENT_EVIDENCE'
    elif mode == 'negative':
        source['negative_pairs'] = [('a', 'c')]
    elif mode == 'known-missing':
        source['known_conflicts'] = [{'id': 'prior', 'pair_id': 'not-current', 'reason': 'PRIOR_CONFLICT', 'record_ids': ['x']}]
    aid = freeze(client, workflow_run, source)
    with engine_context(source['profile']), client.session_factory() as db:
        expected = compute_publication(deepcopy(source), workflow_run, 1, virtual_singletons=True)
        with open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
            actual = StreamedPlan(view, workflow_run, 1)
            assert {tuple(g['member_record_ids']) for g in actual.iter_groups()} == {
                tuple(g['member_record_ids']) for g in expected['clusters']}
            assert {r['id'] for r in actual.iter_eligible_records()} == {
                r['id'] for r in records if r['id'] not in expected['conflict_record_ids']}
            for key, value in actual.counts().items():
                assert value == expected['publication'][key], (mode, key)


def test_streamed_plan_rejects_pair_endpoint_outside_frozen_universe(client, workflow_run):
    from masterone.mdm.workflow_streamed_plan import StreamedPlan
    records = [record('a'), record('outside')]
    pair = edge(*records)
    source = dict(records=records[:1], pairs=[pair], overlays={pair['id']: 'ACCEPTED'},
                  negative_pairs=[], policy_version='person-v3', profile={})
    aid = freeze(client, workflow_run, source)
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
        with pytest.raises(ValueError, match='WORKFLOW_PAIR_UNIVERSE_INVALID'):
            StreamedPlan(view, workflow_run, 1)


def test_streamed_plan_keeps_full_reviewable_group_inputs_and_paged_evidence(client, workflow_run):
    from masterone.mdm.workflow_cluster_preview import review_group_page
    from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowOperation
    from masterone.mdm.workflow_streamed_plan import StreamedPlan

    from tests.test_mdm.test_large_group_review import large_snapshot
    source = large_snapshot(21)
    source['profile'] = {'cluster_safety_v2_enabled': True, 'decision_semantics_v2_enabled': True}
    aid = freeze(client, workflow_run, source)
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view, engine_context(source['profile']):
        plan = StreamedPlan(view, workflow_run, 1)
        reviews = list(plan.iter_reviews())
        assert len(reviews) == 1
        draft = reviews[0]
        assert len(draft['records']) == 21
        pid = seal_snapshot(client.session_factory, workflow_run, 'CLUSTER_PLAN', {},
            {'review_groups': iter(reviews)}, operation_id=view.row.payload['operation_id'])
    with client.session_factory.begin() as db:
        op = db.get(WorkflowOperation, view.row.payload['operation_id'])
        op.operation_type = 'CLUSTER_PREVIEW'
        op.result = {'streamed_preview': True, 'plan_artifact_id': pid,
                     'plan_storage_hash': db.get(WorkflowArtifact, pid).content_hash,
                     'source_decision_revision': 0, 'source_assessment_id': None}
        run = db.get(__import__('masterone.mdm.models', fromlist=['ResolutionRun']).ResolutionRun, workflow_run)
        page = review_group_page(db, run, op.id, draft['fingerprint'], 'evidence', 25, 100)
        assert page['total'] == 210
        assert len(page['items']) == 25
        assert any(p['computed_only'] for p in page['items'])
        members = review_group_page(db, run, op.id, draft['fingerprint'], 'members', 25, 20)
        assert members['items'][0]['record_id'] == '020'
