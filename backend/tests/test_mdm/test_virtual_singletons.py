from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from masterone.mdm.generation_staging import digest
from masterone.mdm.models import ResolutionEntity, ResolutionRecord, ResolutionRun
from masterone.mdm.workflow_artifacts import seal
from masterone.mdm.workflow_models import WorkflowCluster, WorkflowGeneration, WorkflowMember

from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_stages import ready_decisions, run_one


def virtual_fixture(client, run_id, count=1000, excluded=()):
    from masterone.mdm.pipeline.golden_v2 import record_payload
    ready_decisions(client, run_id)
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        original = db.query(ResolutionRecord).filter_by(run_id=run_id).first()
        records = [record_payload(original)] if count else []
        for i in range(1, count):
            row = ResolutionRecord(id=f'virtual-{i:06}', run_id=run_id,
                file_id=original.file_id, source_system='CRM', external_id=f'virtual-source-{i}', raw_fields=deepcopy(original.raw_fields),
                normalized_fields=deepcopy(original.normalized_fields), extra_fields={}, quality_flags=[], entity_type='PERSON')
            db.add(row)
            records.append(record_payload(row))
        run.record_count = count
        decision_revision = run.decision_revision
    eligible = sorted((r for r in records if r['id'] not in excluded), key=lambda r: r['id'])
    profile = dict(cluster_safety_v2_enabled=True, decision_semantics_v2_enabled=True, generation_staging_enabled=False)
    from dataclasses import asdict

    from masterone.mdm.pipeline.cluster_policy import load_policy
    source = {'records': eligible, 'profile': profile, 'survivorship_policy': asdict(load_policy())}
    artifact_id = seal(client.session_factory, run_id, 'SINGLETON_SOURCE', source)
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.generation_staging import config_identity
    with engine_context(profile):
        execution = digest(config_identity('person-v3'))
    snapshot = {'records': eligible, 'profile': profile, 'pairs': [], 'overlays': {}, 'negative_pairs': [],
                'decision_revision': decision_revision, 'policy_version': 'person-v3', 'execution_identity': execution}
    seal(client.session_factory, run_id, 'DECISION_SNAPSHOT', snapshot)
    output_id = seal(client.session_factory, run_id, 'CLUSTER', {'workflow_context': {'decision_revision': decision_revision,
        'assessment_id': None, 'snapshot_hash': digest(snapshot)}, 'pairs': [], 'conflict_record_ids': [], 'conflicts': [], 'clusters': []})
    with client.session_factory.begin() as db:
        run = db.get(ResolutionRun, run_id)
        run.cluster_revision = 1
        run.workflow_state = 'GOLDEN_REVIEW'
        generation = WorkflowGeneration(run_id=run_id, revision=1, artifact_id=output_id,
            storage_version='workflow_virtual_v2', coverage={'clusters': len(eligible), 'singletons': len(eligible),
                'multi_record_groups': 0, 'covered_records': len(eligible), 'total_records': count,
                'unresolved_records': count-len(eligible), 'source_artifact_id': artifact_id,
                'source_hash': digest(source)}, singleton_confirmation={})
        db.add(generation)
        db.flush()
        return generation.id, eligible


@pytest.mark.parametrize('count', [0, 1, 1000])
def test_virtual_coverage_pages_and_no_rows(client, workflow_run, count):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    gid, records = virtual_fixture(client, workflow_run, count)
    with client.session_factory() as db:
        run, gen = db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid)
        reader = GenerationReader(db, run, gen)
        assert reader.coverage()['covered_records'] == count
        assert reader.unconfirmed_count() == count
        assert db.query(WorkflowCluster).count() == db.query(WorkflowMember).count() == 0
        ids, cursor = [], None
        while True:
            page = reader.group_page(100, cursor)
            ids.extend(r['member_record_ids'][0] for r in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        assert ids == sorted(r['id'] for r in records)


@pytest.mark.parametrize('after', [None, 'virtual-000900'])
def test_legacy_virtual_records_verify_each_partition_once_per_iteration(client, workflow_run, monkeypatch, after):
    from collections import Counter

    from masterone.mdm import workflow_artifacts
    from masterone.mdm.workflow_generation_reads import GenerationReader
    gid, records = virtual_fixture(client, workflow_run, 1100)
    verified = Counter()
    original = workflow_artifacts.digest

    def counted(payload):
        if set(payload) == {'items'}:
            verified[payload['items'][0]['id']] += 1
        return original(payload)

    monkeypatch.setattr(workflow_artifacts, 'digest', counted)
    with client.session_factory() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        actual = list(reader.records(after=after))
    assert verified and max(verified.values()) == 1
    assert [r['id'] for r in actual] == [r['id'] for r in records if after is None or r['id'] > after]


def test_virtual_ownership_eligibility_and_frozen_source(client, workflow_run):
    from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id
    gid, records = virtual_fixture(client, workflow_run, 4, ['virtual-000003'])
    rid = records[0]['id']
    with client.session_factory.begin() as db:
        row = db.get(ResolutionRecord, rid)
        row.raw_fields = {**row.raw_fields, 'name': 'changed live source'}
    with client.session_factory() as db:
        run, gen = db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid)
        reader = GenerationReader(db, run, gen)
        assert reader.record(rid)['raw_fields']['name'] == records[0]['raw_fields']['name']
        assert reader.group(virtual_cluster_id(gid, rid))['member_record_ids'] == [rid]
        for cid in [virtual_cluster_id('other-generation', rid), virtual_cluster_id(gid, 'virtual-000003')]:
            with pytest.raises(HTTPException) as error:
                reader.group(cid)
            assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            GenerationReader(db, SimpleNamespace(id='another-run'), gen)


def test_real_publication_keeps_untouched_singletons_virtual(client, workflow_run):
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    page = client.get(root + '/clusters').json()
    singles = [g for g in page['items'] if g['singleton']]
    assert singles and all(g['cluster_id'].startswith('s:') for g in singles)
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == page['generation']['multi_record_groups']
        assert db.query(ResolutionEntity).count() == page['generation']['multi_record_groups']
    golden = client.get(root + '/golden').json()
    assert len(golden['items']) == page['generation']['clusters']


def test_bulk_receipt_and_edit_override_without_singleton_writes(client, workflow_run, monkeypatch):
    from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id

    from tests.test_mdm.test_workflow_golden import golden_request
    gid, records = virtual_fixture(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    # Aggregate confirmation must not build 1,000 singleton Golden drafts.
    with monkeypatch.context() as guard:
        guard.setattr('masterone.mdm.pipeline.golden.build_golden', lambda *_: pytest.fail('N-singleton hydration'))
        response = client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run))
    assert response.status_code == 200, response.text
    assert response.json()['confirmed'] == 1000
    with client.session_factory() as db:
        gen, run = db.get(WorkflowGeneration, gid), db.get(ResolutionRun, workflow_run)
        assert gen.singleton_confirmation['default_confirmed'] is True
        assert GenerationReader(db, run, gen).unconfirmed_count() == 0
        assert db.query(WorkflowCluster).count() == db.query(ResolutionEntity).count() == 0
    cid = virtual_cluster_id(gid, records[-1]['id'])
    response = client.put(root + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'one edited singleton'}}))
    assert response.status_code == 200, response.text
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == db.query(WorkflowMember).count() == db.query(ResolutionEntity).count() == 1
        assert GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid)).unconfirmed_count() == 1
    assert client.get(root + f'/clusters/{cid}/golden').json()['status'] == 'DRAFT'


def test_all_virtual_csv_exact_coverage_and_reopen(client, workflow_run):
    import csv
    from io import StringIO

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_golden import golden_request
    from tests.test_mdm.test_workflow_stages import read
    gid, records = virtual_fixture(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}'
    root = base + '/workflow'
    response = client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run))
    assert response.status_code == 200, response.text
    state = read(client, workflow_run)
    response = client.post(root + '/finalize-golden', json=request(state['workflow_revision'],
        expected_golden_revision=state['golden_revision']))
    assert response.status_code == 200, response.text
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    exported = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute', json={'execute_request_id': 'virtual-export'})
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 1000
    ids = [rid for row in rows for rid in row['source_record_ids'].split(';')]
    assert len(ids) == len(set(ids)) == 1000
    assert set(ids) == {r['id'] for r in records}
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0
    state = read(client, workflow_run)
    assert client.post(root + '/reopen-decisions', json=request(state['workflow_revision'], reason='review again')).status_code == 200
    page = client.get(root + '/clusters').json()
    assert page['generation']['covered_records'] == 1000
    assert page['generation']['read_only'] is True
    assert page['generation']['stale'] is True


def test_excluded_virtual_exception_overrides_bulk_default(client, workflow_run):
    from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id

    from tests.test_mdm.test_workflow_golden import golden_request
    gid, records = virtual_fixture(client, workflow_run, 4)
    cid = virtual_cluster_id(gid, records[-1]['id'])
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run,
        excluded_cluster_ids=[cid]))
    assert response.status_code == 200, response.text
    assert response.json()['confirmed'] == 3
    with client.session_factory() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        assert reader.unconfirmed_count() == 1
        assert reader.row(cid).confirmed is False
        assert db.query(WorkflowCluster).count() == 1


def test_virtual_golden_policy_is_pinned(client, workflow_run, monkeypatch):
    from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id
    gid, records = virtual_fixture(client, workflow_run, 1)
    with client.session_factory() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        cid = virtual_cluster_id(gid, records[0]['id'])
        expected = reader.row(cid).golden
    monkeypatch.setattr('masterone.mdm.pipeline.golden_v2.load_policy', lambda: pytest.fail('unfrozen policy'))
    with client.session_factory() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        assert reader.row(cid).golden == expected


def test_resolve_all_virtual_uses_one_receipt(client, workflow_run):
    from tests.test_mdm.test_workflow_golden import golden_request
    from tests.test_mdm.test_workflow_stages import read
    gid, _ = virtual_fixture(client, workflow_run, 30)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    state = read(client, workflow_run)
    response = client.post(root + '/golden/resolve', json=golden_request(client, workflow_run,
        expected_decision_revision=state['decision_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    op = client.get(root + '/operations/' + response.json()['operation_id']).json()
    assert op['result']['confirmed'] == 30
    with client.session_factory() as db:
        assert db.get(WorkflowGeneration, gid).singleton_confirmation['default_confirmed'] is True
        assert db.query(WorkflowCluster).count() == 0


def test_real_thousand_singletons_publish_confirm_finalize_export(client, monkeypatch):
    import csv
    from io import StringIO

    from masterone.config import get_settings

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_golden import golden_request
    from tests.test_mdm.test_workflow_stages import read
    monkeypatch.setattr(get_settings(), 'session_workflow_enabled', True)
    from hashlib import sha256
    def unique_name(i):
        return sha256(str(i).encode()).hexdigest()[:20].translate(str.maketrans('0123456789abcdef', 'abcdefghijklmnop'))
    csv_input = 'source_record_id,source_system,name,email\n' + ''.join(
        f'{i},CRM,{unique_name(i)},unique{i}@source{i}.test\n' for i in range(1000))
    created = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
        files={'files': ('singletons.csv', csv_input.encode(), 'text/csv')})
    assert created.status_code == 201, created.text
    run_id = created.json()['run_id']
    ready_decisions(client, run_id)
    complete_reviews(client, run_id)
    assert finalize(client, run_id).status_code == 202
    run_one(client)
    base = f'/api/v1/mdm/runs/{run_id}'
    root = base + '/workflow'
    page = client.get(root + '/clusters').json()
    assert page['generation']['covered_records'] == page['generation']['singletons'] == 1000
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == db.query(WorkflowMember).count() == db.query(ResolutionEntity).count() == 0
    response = client.post(root + '/golden/confirm-selected', json=golden_request(client, run_id))
    assert response.status_code == 200, response.text
    assert response.json()['confirmed'] == 1000
    state = read(client, run_id)
    response = client.post(root + '/finalize-golden', json=request(state['workflow_revision'], expected_golden_revision=state['golden_revision']))
    assert response.status_code == 200, response.text
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    response = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute', json={'execute_request_id': 'real-thousand'})
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows) == len({r['source_record_ids'] for r in rows}) == 1000
    assert all(int(r['record_count']) == 1 for r in rows)


def test_mixed_paging_replaces_virtual_members_without_duplicates(client, workflow_run):
    from masterone.mdm.workflow_generation_reads import GenerationReader, virtual_cluster_id
    gid, records = virtual_fixture(client, workflow_run, 12)
    with client.session_factory.begin() as db:
        run, gen = db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid)
        reader = GenerationReader(db, run, gen)
        members = [r['id'] for r in records[:2]]
        payload = {**reader.row(virtual_cluster_id(gid, members[0])).payload,
                   'id': 'materialized-group', 'member_record_ids': members, 'singleton': False}
        db.add(WorkflowCluster(id=payload['id'], generation_id=gid, payload=payload,
                               golden=payload['golden_fields'], selections={}, confirmed=False))
        db.flush()
        db.add_all([WorkflowMember(generation_id=gid, cluster_id=payload['id'], record_id=rid) for rid in members])
        gen.coverage = {**gen.coverage, 'clusters': 11, 'singletons': 10, 'multi_record_groups': 1}
    with client.session_factory() as db:
        reader = GenerationReader(db, db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid))
        cursor, groups = None, []
        while True:
            page = reader.group_page(5, cursor)
            groups.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        assert len(groups) == 11
        covered = [rid for group in groups for rid in group['member_record_ids']]
        assert len(covered) == len(set(covered)) == 12
        with pytest.raises(HTTPException):
            reader.group(virtual_cluster_id(gid, members[0]))


def test_aggregate_counts_do_not_hydrate_group_or_source_rows(client, workflow_run, monkeypatch):
    from masterone.mdm.workflow_cluster_reads import generation_metadata
    from masterone.mdm.workflow_generation_reads import GenerationReader
    gid, _ = virtual_fixture(client, workflow_run, 1000)
    monkeypatch.setattr(GenerationReader, 'rows', lambda *_: pytest.fail('group hydration'))
    monkeypatch.setattr(GenerationReader, 'records', lambda *_: pytest.fail('source hydration'))
    with client.session_factory() as db:
        run, gen = db.get(ResolutionRun, workflow_run), db.get(WorkflowGeneration, gid)
        from sqlalchemy import event
        statements = []
        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(db.bind, 'before_cursor_execute', capture)
        try:
            assert generation_metadata(db, run, gen)['singletons'] == 1000
            assert GenerationReader(db, run, gen).unconfirmed_count() == 1000
        finally:
            event.remove(db.bind, 'before_cursor_execute', capture)
        assert len(statements) <= 4


def test_source_choice_reads_pinned_value_after_live_edit(client, workflow_run):
    from masterone.mdm.workflow_generation_reads import virtual_cluster_id

    from tests.test_mdm.test_workflow_golden import golden_request
    gid, records = virtual_fixture(client, workflow_run, 1)
    rid, original_name = records[0]['id'], records[0]['raw_fields']['name']
    cid = virtual_cluster_id(gid, rid)
    with client.session_factory.begin() as db:
        row = db.get(ResolutionRecord, rid)
        row.raw_fields = {**row.raw_fields, 'name': 'Live edit must not replace frozen choice'}
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    response = client.put(root + f'/clusters/{cid}/golden', json=golden_request(client, workflow_run,
        selections={'name': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': rid, 'value': original_name}}))
    assert response.status_code == 200, response.text
    golden = client.get(root + f'/clusters/{cid}/golden').json()
    assert golden['fields']['name']['value'] == original_name
    assert golden['alternatives']['name'][0]['value'] == original_name
