from dataclasses import replace

import pytest
from masterone.config import get_settings
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.cluster_policy import load_policy
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowGeneration

from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_golden import golden_request, ready_golden
from tests.test_mdm.test_workflow_stages import read, run_one


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), 'workflow_parquet_enabled', True)


def test_new_publication_uses_owned_streamed_snapshot_and_generation(client, workflow_run):
    from masterone.mdm.models import ResolutionRun
    ready_golden(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=run.cluster_revision).one()
        assert generation.storage_version == 'workflow_parquet_v2'
        output = db.get(WorkflowArtifact, generation.artifact_id)
        assert output.payload['format'] == 'p119_workflow_snapshot_v2'
        source = db.get(WorkflowArtifact, generation.coverage['source_artifact_id'])
        assert source.payload['format'] == 'p119_workflow_snapshot_v2'


def test_new_finalization_is_small_receipt_and_csv_still_exact(client, workflow_run):
    import csv
    from io import StringIO
    ready_golden(client, workflow_run)
    base = f'/api/v1/mdm/runs/{workflow_run}'
    root = base + '/workflow'
    assert client.post(root + '/golden/confirm-selected', json=golden_request(client, workflow_run)).status_code == 200
    state = read(client, workflow_run)
    response = client.post(root + '/finalize-golden', json=request(state['workflow_revision'],
        expected_golden_revision=state['golden_revision']))
    assert response.status_code == 202, response.text
    run_one(client)
    with client.session_factory() as db:
        generation = db.query(WorkflowGeneration).filter_by(run_id=workflow_run, revision=state['cluster_revision']).one()
        assert generation.finalized_result['version'] == 'p119_workflow_final_v2'
        assert 'clusters' not in generation.finalized_result and 'records' not in generation.finalized_result
        assert generation.finalized_result['master_id_version'] == 'workflow_entity_v1'
    preview = client.post(base + '/previews', json={'choices': {}, 'acknowledge_unreviewed': False})
    assert preview.status_code == 200, preview.text
    exported = client.post(base + '/previews/' + preview.json()['preview_id'] + '/execute',
                           json={'execute_request_id': 'streamed-export'})
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
    assert sum(int(r['record_count']) for r in rows) == 3
    covered = [rid for row in rows for rid in row['source_record_ids'].split(';')]
    assert len(covered) == len(set(covered)) == 3


def test_approved_groups_share_one_run_guard_budget(monkeypatch):
    from masterone.mdm.workflow_finalize import cluster_snapshot
    from masterone.mdm.workflow_group_approvals import approval_fingerprint

    from tests.test_mdm.test_pair_classification_regressions import record
    from tests.test_mdm.test_safe_clustering import edge
    records = [record(rid) for rid in ['a', 'b', 'x', 'y']]
    pairs = [{**edge(records[i], records[i + 1]), 'decision': 'REVIEW'} for i in (0, 2)]
    source = {'records': records, 'pairs': pairs, 'overlays': {p['id']: 'ACCEPTED' for p in pairs},
              'negative_pairs': [], 'review_reasons': {}, 'policy_version': 'person-v3', 'group_approvals': []}
    for i, ids in enumerate((['a', 'b'], ['x', 'y'])):
        source['group_approvals'].append({'operation_id': f'approval-{i}', 'record_ids': ids,
            'reason': 'explicit complete review', 'fingerprint': approval_fingerprint(source, ids)})
    policy = replace(load_policy(), max_pair_checks_per_run=1)
    monkeypatch.setattr('masterone.mdm.pipeline.cluster_policy.load_policy', lambda **_: policy)
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                             generation_staging_enabled=False)):
        result = cluster_snapshot(source, 'budget')
    assert len(result['safe_clusters']) == 1
    assert result['conflicts']
    assert result['cluster_metrics']['total_checked'] == 1
