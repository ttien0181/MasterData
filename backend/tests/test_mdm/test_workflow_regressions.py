from copy import deepcopy
from itertools import permutations

import pytest
from fastapi import HTTPException
from masterone.config import get_settings
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.models import ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowPair

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_golden import golden_request, ready_golden
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


@pytest.mark.parametrize('v2', [False, True])
def test_workflow_auto_and_manual_equivalence(v2, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from masterone.mdm.pipeline.clustering import build_proposals
    from masterone.mdm.pipeline.golden import build_golden
    from masterone.mdm.pipeline.golden_v2 import golden_payload
    from masterone.mdm.workflow_finalize import cluster_snapshot

    from tests.test_mdm.test_decision_semantics import record
    from tests.test_mdm.test_safe_clustering import edge
    rows = [record('a'), record('b')]
    rows[0]['source_system'], rows[1]['source_system'] = 'BILL', 'CRM'
    pair = edge(*rows)
    pair['triage_status'] = 'HIGH_CONFIDENCE_PROPOSAL'
    with engine_context(dict(decision_semantics_v2_enabled=v2, cluster_safety_v2_enabled=v2, generation_staging_enabled=False)):
        baseline = build_proposals(rows, [deepcopy(pair)], 'r', 'v')['proposals']
        for ordered in permutations(rows):
            snapshot = {'records': list(ordered), 'pairs': [pair], 'overlays': {}, 'negative_pairs': [], 'policy_version': 'v'}
            assert cluster_snapshot(snapshot, 'r')['clusters'] == baseline
        pair['decision'] = 'REVIEW'
        snapshot = {'records': rows, 'pairs': [pair], 'overlays': {pair['id']: 'ACCEPTED'}, 'negative_pairs': [], 'policy_version': 'v'}
        manual = cluster_snapshot(snapshot, 'r')
        assert not manual['conflicts']
        assert manual['clusters'][0]['member_record_ids'] == ['a', 'b']
        assert manual['clusters'][0]['golden_fields'] == build_golden(['a', 'b'], {r['id']: golden_payload(SimpleNamespace(**r)) for r in rows})


def test_workflow_corrupt_artifact_does_not_publish(client, workflow_run):
    from masterone.mdm.job_queue import claim_next, ownership
    from masterone.mdm.workflow_jobs import execute_workflow
    from sqlalchemy import update
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    response = finalize(client, workflow_run)
    claim = claim_next(client.session_factory, 'corrupt', lease_seconds=300)
    from masterone.mdm.workflow_cluster_preview import operation_snapshot
    with ownership(claim):
        operation_snapshot(response.json()['operation_id'], client.session_factory, lambda *_: None)
    with client.session_factory.begin() as db:
        op_id = db.get(ResolutionTask, claim.task_id).operation_id
        from masterone.mdm.workflow_models import WorkflowOperation
        artifact_id = db.get(WorkflowOperation, op_id).artifact_id
        db.execute(update(WorkflowArtifact).where(WorkflowArtifact.id == artifact_id).values(content_hash='0' * 64))
    with ownership(claim), pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_INVALID'):
        execute_workflow(response.json()['operation_id'], client.session_factory, lambda *_: None)
    with client.session_factory() as db:
        assert db.query(WorkflowCluster).count() == 0


def test_workflow_defer_blocks_finalize_and_empty_coverage(client, workflow_run):
    state = ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = next(p for p in client.get(url + '/pairs').json()['items'] if p['baseline']['decision'] not in ('MERGEABLE', 'BLOCKED', 'NON_MATCH', 'NOT_MERGEABLE'))
    response = client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'], expected_pair_revision=pair['revision'], decision='defer'))
    assert response.status_code == 200
    assert finalize(client, workflow_run).status_code == 409
    with client.session_factory.begin() as db:
        db.get(ResolutionRun, workflow_run).record_count = 0
    assert any(b['code'] == 'NO_VALID_RECORDS' for b in read(client, workflow_run)['blockers'])


def test_workflow_insufficient_never_auto_merges_and_group_d_policy(client, workflow_run):
    from masterone.mdm.workflow_review import eligibility
    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        row = db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        insufficient = WorkflowPair(id='insufficient-test', run_id=workflow_run,
            baseline={**row.baseline, 'id': 'insufficient-test', 'decision': 'INSUFFICIENT_EVIDENCE'}, decision='UNDECIDED')
        db.add(insufficient)
        db.flush()
        assert eligibility(insufficient)['auto_allowed'] is False
        assert eligibility(WorkflowPair(baseline={**row.baseline, 'decision': 'REVIEW', 'primary_group': 'D'}, decision='UNDECIDED'))['auto_allowed'] is False
    # Retained insufficient candidates cannot auto-merge and do not manufacture
    # mandatory human work. Genuine D review remains covered independently.
    assert finalize(client, workflow_run).status_code == 202


def test_workflow_zero_candidate_singleton(client, monkeypatch):
    monkeypatch.setattr(get_settings(), 'session_workflow_enabled', True)
    result = client.post('/api/v1/mdm/runs', data={'workflow_version': '2'},
        files={'files': ('one.csv', b'source_record_id,source_system,name,phone,email,address\n1,CRM,An,0901234567,a@example.test,Hanoi\n', 'text/csv')})
    rid = result.json()['run_id']
    state = ready_decisions(client, rid)
    assert state['counts']['candidates']['value'] == 0
    assert finalize(client, rid).status_code == 202
    run_one(client)
    clusters = client.get(f'/api/v1/mdm/runs/{rid}/workflow/clusters').json()['items']
    assert len(clusters) == 1 and clusters[0]['singleton']


def test_workflow_auth_csrf_origin_owner_and_paging(client, workflow_run):
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    assert client.get(url + '/pairs?limit=101').status_code == 422
    assert client.get(url + '/pairs?limit=0').status_code == 422
    headers = dict(client.headers)
    client.headers.pop('X-CSRF-Token')
    assert client.post(url + '/start-decide', json=request()).status_code == 403
    client.headers.update(headers)
    assert client.post(url + '/start-decide', json=request(), headers={'Origin': 'https://hostile.example'}).status_code == 403
    from masterone.auth.models import User
    with client.session_factory.begin() as db:
        owner = db.get(User, db.get(ResolutionRun, workflow_run).owner_user_id)
        owner.role = 'VIEWER'
    assert client.get(url).json()['allowed_actions'] == []
    assert client.post(url + '/start-decide', json=request()).status_code == 403


def test_workflow_source_choice_multivalue_extra_and_confirmation(client, workflow_run):
    # Supplementary sources must be present before the official snapshot freezes.
    with client.session_factory.begin() as db:
        row = db.query(ResolutionRecord).filter_by(run_id=workflow_run).order_by(ResolutionRecord.id).first()
        row.extra_fields = {**row.extra_fields, 'custom_field': 'Source Extra'}
    clusters = ready_golden(client, workflow_run)
    current = read(client, workflow_run)
    assert 'finalize-golden' not in current['allowed_actions']
    assert current['blockers'][0]['code'] == 'UNCONFIRMED_GOLDEN'
    cluster = next(c for c in clusters if c['member_count'] > 1)
    cid = cluster['cluster_id']
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    body = golden_request(client, workflow_run, selections={'custom_field': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': row.id}})
    assert client.put(url + f'/clusters/{cid}/golden', json=body).status_code == 200
    golden = client.get(url + f'/clusters/{cid}/golden').json()
    assert golden['fields']['custom_field']['value'] == 'Source Extra'
    assert golden['alternatives']['custom_field'][0]['auto_rule_available'] is False
    assert golden['fields']['phone']['values']
    state = read(client, workflow_run)
    assert client.post(url + '/finalize-golden', json=request(state['workflow_revision'], expected_golden_revision=state['golden_revision'])).status_code == 409


def test_workflow_cluster_evidence_paging(client, workflow_run):
    clusters = ready_golden(client, workflow_run)
    cid = next(c['cluster_id'] for c in clusters if c['member_count'] > 1)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow/clusters/{cid}'
    first = client.get(url + '?evidence_limit=1').json()['evidence']
    assert len(first['items']) <= 1
    assert first['total'] >= 1
    assert client.get(url + '?evidence_limit=101').status_code == 422


def test_workflow_full_runner_direct_cannot_bypass(client, workflow_run):
    from masterone.mdm.resolution_service import _compute, create_task, run_pipeline
    with pytest.raises(HTTPException) as e:
        run_pipeline(workflow_run, client.session_factory, lambda *_: None)
    assert e.value.status_code == 409
    with pytest.raises(HTTPException):
        create_task(workflow_run, client.session_factory)
    with pytest.raises(HTTPException):
        _compute([], workflow_run, 'person-v3', _checkpoint_factory=client.session_factory)


def test_workflow_defer_auto_pair_blocks_completion(client, workflow_run):
    state = ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair = next(p for p in client.get(url + '/pairs').json()['items'] if p['baseline']['decision'] == 'MERGEABLE')
    assert client.post(url + f"/pairs/{pair['pair_id']}/decision", json=request(state['workflow_revision'],
        expected_pair_revision=pair['revision'], decision='defer')).status_code == 200
    complete_reviews(client, workflow_run)
    assert finalize(client, workflow_run).status_code == 409
