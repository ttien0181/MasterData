"""Optional enrichment is a separate fenced job and never changes group decisions."""
from copy import deepcopy

import pytest
from masterone.config import get_settings
from masterone.llm.policy import PolicyDenied
from masterone.mdm.job_queue import OwnershipLostError
from masterone.mdm.workflow_models import WorkflowCluster

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_finalize import complete_reviews, finalize
from tests.test_mdm.test_workflow_stages import read, ready_decisions, run_one


def ready(client, workflow_run, monkeypatch):
    monkeypatch.setattr(get_settings(), 'llm_enabled', True)
    monkeypatch.setattr(get_settings(), 'semantic_address_enabled', True)
    ready_decisions(client, workflow_run)
    complete_reviews(client, workflow_run)
    assert finalize(client, workflow_run).status_code == 202
    run_one(client)
    state = read(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    queued = client.post(root + '/group-annotations', json=request(state['workflow_revision'],
        expected_cluster_revision=state['cluster_revision']))
    assert queued.status_code == 202, queued.text
    return root, state, queued.json()


@pytest.mark.parametrize('failure', ['policy', 'service'])
def test_optional_job_failure_cannot_change_published_groups_or_golden(client, workflow_run, monkeypatch, failure):
    root, state, queued = ready(client, workflow_run, monkeypatch)
    with client.session_factory() as db:
        before = {c.id: (deepcopy(c.golden), deepcopy(c.selections), c.confirmed,
                        c.payload['member_record_ids']) for c in db.query(WorkflowCluster)}

    def unavailable(*_args, **_kwargs):
        raise PolicyDenied('CIRCUIT_OPEN') if failure == 'policy' else RuntimeError('provider unavailable')
    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', unavailable)
    run_one(client)
    receipt = client.get(root + '/operations/' + queued['operation_id']).json()
    assert receipt['status'] == 'COMPLETED'
    assert receipt['result']['stats']['stopped'] == ('CIRCUIT_OPEN' if failure == 'policy' else 'ANNOTATION_UNAVAILABLE')
    after = read(client, workflow_run)
    assert after['cluster_revision'] == state['cluster_revision']
    assert after['golden_revision'] == state['golden_revision']
    assert after['decision_revision'] == state['decision_revision']
    assert after['annotation_revision'] == queued['operation_id']
    with client.session_factory() as db:
        assert before == {c.id: (c.golden, c.selections, c.confirmed,
                                c.payload['member_record_ids']) for c in db.query(WorkflowCluster)}


def test_optional_job_never_swallows_worker_ownership_loss(client, workflow_run, monkeypatch):
    ready(client, workflow_run, monkeypatch)
    def fenced(*_args, **_kwargs):
        raise OwnershipLostError('JOB_OWNER_FENCED')
    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', fenced)
    with pytest.raises(OwnershipLostError):
        run_one(client)


def test_annotation_reads_verified_partitions_without_loading_full_reports(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_group_annotations, workflow_resolution
    from masterone.mdm.pipeline.semantic_address import SemanticResult

    root, _, queued = ready(client, workflow_run, monkeypatch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Address annotations must not hydrate full CLUSTER/DECISION_SNAPSHOT pair lists')

    monkeypatch.setattr(workflow_group_annotations, 'load', forbidden, raising=False)
    monkeypatch.setattr(workflow_resolution, 'load', forbidden)
    observed = []

    def annotate(pairs, lookup, **_kwargs):
        observed.extend(pairs)
        assert observed and lookup([observed[0]['left_id']])
        return SemanticResult(stats={'checked': len(observed)})

    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', annotate)
    run_one(client)
    receipt = client.get(root + '/operations/' + queued['operation_id']).json()
    assert receipt['status'] == 'COMPLETED'
    assert receipt['result']['stats']['checked'] == len(observed) > 0


def test_annotation_partition_integrity_failure_is_not_an_optional_provider_failure(client, workflow_run, monkeypatch):
    from masterone.mdm import workflow_group_annotations

    ready(client, workflow_run, monkeypatch)
    original = workflow_group_annotations.iter_list

    def corrupt(db, artifact_id, run_id, phase, path, **kwargs):
        if phase == 'CLUSTER' and path == ['pairs']:
            raise ValueError('WORKFLOW_ARTIFACT_INVALID')
        yield from original(db, artifact_id, run_id, phase, path, **kwargs)

    def consume(pairs, *_args, **_kwargs):
        list(pairs)
        raise AssertionError('Corrupt input must fail before a provider request')

    monkeypatch.setattr(workflow_group_annotations, 'iter_list', corrupt)
    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', consume)
    with pytest.raises(ValueError, match='WORKFLOW_ARTIFACT_READ_FAILED'):
        run_one(client)


def test_annotation_sidecar_is_visible_locally_without_altering_plan(client, workflow_run, monkeypatch):
    from masterone.mdm.pipeline.semantic_address import SemanticResult
    root, _, queued = ready(client, workflow_run, monkeypatch)
    def annotate(pairs, *_args, **_kwargs):
        pairs = list(pairs)
        return SemanticResult(annotations={p['id']: {'field': 'address', 'result': 'EQUIVALENT',
            'eligible_for_auto_merge': False} for p in pairs}, stats={'checked': len(pairs)})
    monkeypatch.setattr('masterone.mdm.pipeline.semantic_address.run_annotations', annotate)
    run_one(client)
    assert client.get(root + '/operations/' + queued['operation_id']).json()['status'] == 'COMPLETED'
    groups = client.get(root + '/clusters').json()['items']
    group = next(g for g in groups if g['member_count'] > 1)
    detail = client.get(root + '/clusters/' + group['cluster_id']).json()
    assert detail['pair_evidence']['items'][0]['semantic_annotations'][0]['eligible_for_auto_merge'] is False
