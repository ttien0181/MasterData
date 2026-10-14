"""Global lineage decisions must survive partitioning and iteration order."""
from types import SimpleNamespace

import pytest
from masterone.mdm.models import ResolutionEntityMember, ResolutionEntityVersion, ResolutionRecord, ResolutionRun
from masterone.mdm.pipeline.golden_v2 import record_payload
from masterone.mdm.workflow_models import WorkflowGeneration
from masterone.mdm.workflow_snapshot_storage import open_snapshot

from tests.test_mdm.test_workflow_entity_identity import group, identity_state, publish_identity
from tests.test_mdm.test_workflow_streamed_plan import freeze


@pytest.mark.parametrize('mode', ['split', 'merge', 'unchanged', 'overlap-tie'])
def test_disk_lineage_plan_matches_whole_generation_plan(client, workflow_run, mode):
    from masterone.mdm.workflow_entities import plan_entity_ids
    from masterone.mdm.workflow_streamed_entities import StreamedEntities
    from masterone.mdm.workflow_streamed_plan import StreamedPlan
    if mode == 'overlap-tie':
        with client.session_factory.begin() as db:
            original = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
            db.add(ResolutionRecord(id='00000000-0000-0000-0000-000000000000', run_id=workflow_run,
                file_id=original.file_id, source_system='CRM', external_id='tie-new', entity_type='PERSON',
                raw_fields=dict(original.raw_fields), normalized_fields={}, quality_flags=[], extra_fields={}))
    with client.session_factory() as db:
        records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)]
    aid = freeze(client, workflow_run, dict(records=records, pairs=[], overlays={}, negative_pairs=[],
                 profile={}, policy_version='person-v3'))
    ids = identity_state(client, workflow_run)
    old_sets = [ids[1:]] if mode == 'overlap-tie' else [ids] if mode == 'split' else [ids[:2], ids[2:]]
    new_sets = [[ids[0],ids[3]],[ids[2]]] if mode == 'overlap-tie' else [[ids[2]], ids[:2]] if mode == 'split' else [ids] if mode == 'merge' else old_sets
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, workflow_run)
        publish_identity(db, run, old_sets, 1)
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
        run = db.get(ResolutionRun, workflow_run)
        plan = StreamedPlan(view, workflow_run, 2)
        groups = [group('g2-' + str(i), members) for i, members in enumerate(new_sets)]
        for item in reversed(groups):
            plan._put('plan_groups', item['id'], item)
            plan.connection.executemany('INSERT INTO plan_members VALUES (?,?)', ((rid, item['id']) for rid in item['member_record_ids']))
        generation = SimpleNamespace(id='new-generation', revision=2)
        expected = plan_entity_ids(db, run, generation, groups)
        StreamedEntities(db, run, plan).prepare(generation)
        actual = list(plan.iter_groups())
        keys = ('entity_id', 'anchor_record_id', 'ancestor_entity_ids', 'retained_ancestor_cluster_ids')
        assert {g['id']: {k: g[k] for k in keys} for g in actual} == {
            g['id']: {k: g[k] for k in keys} for g in expected}


def test_streamed_entity_writes_are_transactional_and_keep_older_history(client, workflow_run):
    from masterone.mdm.workflow_streamed_entities import StreamedEntities
    from masterone.mdm.workflow_streamed_plan import StreamedPlan
    with client.session_factory() as db:
        records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)]
    aid = freeze(client, workflow_run, dict(records=records, pairs=[], overlays={}, negative_pairs=[], profile={}, policy_version='person-v3'))
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        parent = publish_identity(db, db.get(ResolutionRun, workflow_run), [ids], 1)[0]
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
        run = db.get(ResolutionRun, workflow_run)
        plan = StreamedPlan(view, workflow_run, 2)
        for i, members in enumerate([ids[:2], ids[2:]]):
            item = group('split-' + str(i), members)
            plan._put('plan_groups', item['id'], item)
            plan.connection.executemany('INSERT INTO plan_members VALUES (?,?)', ((rid, item['id']) for rid in members))
        generation = WorkflowGeneration(id='next-gen', run_id=run.id, revision=2, artifact_id=aid)
        db.add(generation)
        db.flush()
        entities = StreamedEntities(db, run, plan)
        entities.prepare(generation)
        before_history = {v.id: v.golden_fields for v in db.query(ResolutionEntityVersion)}
        written = list(entities.persist(generation, actor=run.owner_user_id))
        db.flush()
        assert {m.record_id: m.entity_id for m in db.query(ResolutionEntityMember)} == {
            rid: g['entity_id'] for g in written for rid in g['member_record_ids']}
        assert db.query(ResolutionEntityVersion).count() == len(before_history) + 2
        assert all(db.get(ResolutionEntityVersion, key).golden_fields == value for key, value in before_history.items())
        db.rollback()
        assert {m.entity_id for m in db.query(ResolutionEntityMember)} == {parent['entity_id']}
        assert db.query(ResolutionEntityVersion).count() == len(before_history)


def test_split_singleton_exception_participates_in_global_identity_plan(client, workflow_run):
    from masterone.mdm.workflow_generation_reads import virtual_cluster_id
    from masterone.mdm.workflow_streamed_entities import StreamedEntities
    from masterone.mdm.workflow_streamed_plan import StreamedPlan
    with client.session_factory() as db:
        records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)]
    aid = freeze(client, workflow_run, dict(records=records, pairs=[], overlays={}, negative_pairs=[],
        profile={'cluster_safety_v2_enabled': True, 'decision_semantics_v2_enabled': True}, policy_version='person-v3'))
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        parent = publish_identity(db, db.get(ResolutionRun, workflow_run), [ids], 1)[0]
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
        run = db.get(ResolutionRun, workflow_run)
        plan = StreamedPlan(view, workflow_run, 2)
        item = group('multi-child', ids[1:])
        plan._put('plan_groups', item['id'], item)
        plan.connection.executemany('INSERT INTO plan_members VALUES (?,?)', ((rid, item['id']) for rid in ids[1:]))
        generation = SimpleNamespace(id='split-generation', revision=2)
        entities = StreamedEntities(db, run, plan)
        entities.prepare_singletons(generation)
        entities.prepare(generation)
        children = list(plan.iter_groups())
        single = next(g for g in children if g['singleton'])
        assert single['id'] == virtual_cluster_id(generation.id, ids[0])
        assert single['entity_id'] == parent['entity_id']
        assert len({g['entity_id'] for g in children}) == 2
        assert plan.counts()['clusters'] == 2
        assert plan.counts()['singletons'] == 1
        assert plan.counts()['multi_record_groups'] == 1


def test_disk_planner_discovers_implicit_published_singleton_ancestors(client, workflow_run, monkeypatch):
    from masterone.config import get_settings
    from masterone.mdm.workflow_entities import singleton_entity_id
    from masterone.mdm.workflow_models import WorkflowPair
    from masterone.mdm.workflow_streamed_entities import StreamedEntities
    from masterone.mdm.workflow_streamed_plan import StreamedPlan

    from tests.test_mdm.test_workflow_finalize import finalize
    from tests.test_mdm.test_workflow_stages import run_one
    with client.session_factory() as db:
        records = [record_payload(r) for r in db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id)]
    aid = freeze(client, workflow_run, dict(records=records, pairs=[], overlays={}, negative_pairs=[], profile={}, policy_version='person-v3'))
    monkeypatch.setattr(get_settings(), 'workflow_parquet_enabled', True)
    ids = identity_state(client, workflow_run)
    with client.session_factory.begin() as db:
        for row in db.query(WorkflowPair).filter_by(run_id=workflow_run):
            row.decision = 'REJECTED'
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    with client.session_factory() as db, open_snapshot(db, aid, workflow_run, 'DECISION_SNAPSHOT') as view:
        run = db.get(ResolutionRun, workflow_run)
        plan = StreamedPlan(view, run.id, 2)
        item = group('merged', ids[:2])
        plan._put('plan_groups', item['id'], item)
        plan.connection.executemany('INSERT INTO plan_members VALUES (?,?)', ((rid,item['id']) for rid in ids[:2]))
        entities = StreamedEntities(db, run, plan)
        entities.prepare(SimpleNamespace(id='next-generation', revision=2))
        merged = next(plan.iter_groups())
        assert set(merged['ancestor_entity_ids']) == {singleton_entity_id(run.id, rid) for rid in ids[:2]}
        assert merged['entity_id'] == min(merged['ancestor_entity_ids'])
