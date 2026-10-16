"""Optional address enrichment after publication, under the existing durable queue."""
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.job_queue import OwnershipLostError
from masterone.mdm.workflow_artifacts import iter_list, logical_artifact_id, read_value, seal
from masterone.mdm.workflow_cluster_reads import current_generation
from masterone.mdm.workflow_finalize import validate_snapshot_current
from masterone.mdm.workflow_jobs import finish, job_identity
from masterone.mdm.workflow_models import WorkflowCluster


class _ArtifactReadError(ValueError):
    """Integrity/storage failures must not become optional provider failures."""


def run_group_annotations(operation_id, factory, progress):
    from masterone.llm.policy import PolicyDenied
    from masterone.mdm.pipeline.semantic_address import SemanticResult, run_annotations, with_annotations
    with factory.begin() as db:
        run, op, _ = job_identity(db, operation_id)
        generation = current_generation(db, run)
        if run.cluster_revision != op.payload['expected_cluster_revision']:
            raise ValueError('WORKFLOW_CLUSTER_REVISION_CHANGED')
        run_id, generation_id, revision, state = run.id, generation.id, run.workflow_revision, run.workflow_state
        # Locate the publication's actual decision receipt, not an arbitrary latest attempt.
        cluster_artifact_id = generation.artifact_id
        context = read_value(db, cluster_artifact_id, run_id, 'CLUSTER', [])
        snapshot_id = logical_artifact_id(db, run_id, 'DECISION_SNAPSHOT', context['workflow_context']['snapshot_hash'])
        snapshot = dict(read_value(db, snapshot_id, run_id, 'DECISION_SNAPSHOT', []))
        # Freshness checks need metadata, records and negative links, not a second pair universe.
        snapshot['records'] = list(iter_list(db, snapshot_id, run_id, 'DECISION_SNAPSHOT', ['records']))
        snapshot['negative_pairs'] = list(iter_list(db, snapshot_id, run_id, 'DECISION_SNAPSHOT', ['negative_pairs']))
        for key in ('pairs', 'overlays', 'review_reasons', 'known_conflicts', 'group_approvals'):
            snapshot.pop(key, None)
        validate_snapshot_current(db, run, snapshot)
    pair_count = next((part['count'] for part in context.get('__workflow_parts__', [])
                       if part['path'] == ['pairs']), len(context['pairs']))
    progress('annotate-groups', 0, pair_count)
    records = {r['id']: r for r in snapshot['records']}
    with factory() as pair_db, engine_context(snapshot['profile']):
        def verified_pairs():
            try:
                yield from iter_list(pair_db, cluster_artifact_id, run_id, 'CLUSTER', ['pairs'])
            except Exception as error:
                raise _ArtifactReadError('WORKFLOW_ARTIFACT_READ_FAILED') from error

        try:
            semantic = run_annotations(verified_pairs(), lambda ids: {rid: records[rid] for rid in ids if rid in records},
                run_id=run_id, factory=factory, parallel=factory.kw['bind'].dialect.name == 'postgresql')
        except (OwnershipLostError, _ArtifactReadError):
            raise
        except Exception as error:
            # Optional failure is reported without provider text or customer data.
            code = error.code if isinstance(error, PolicyDenied) else 'ANNOTATION_UNAVAILABLE'
            semantic = SemanticResult(stats={'checked': 0, 'errors': 1, 'stopped': code})
    artifact_id = seal(factory, run_id, 'GROUP_ANNOTATIONS',
                       {'generation_id': generation_id, 'annotations': semantic.annotations, 'stats': semantic.stats})
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if run.workflow_revision != revision or run.cluster_revision != op.payload['expected_cluster_revision']:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        validate_snapshot_current(db, run, snapshot)
        for row in db.query(WorkflowCluster).filter_by(generation_id=generation_id).yield_per(100):
            if row.payload.get('local_evidence_version') != 1:
                continue
            row.payload = {**row.payload, 'pair_evidence': list(with_annotations(row.payload['pair_evidence'], semantic.annotations)),
                           'llm_stats': semantic.stats, 'annotation_operation_id': op.id}
        op.artifact_id = artifact_id
        op.result = {'cluster_revision': run.cluster_revision, 'stats': semantic.stats}
        finish(db, run, op, task, state)
