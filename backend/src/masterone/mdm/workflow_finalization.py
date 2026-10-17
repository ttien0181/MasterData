"""Stream frozen Golden rows and register only a small fenced final receipt."""
from contextlib import contextmanager
from sqlite3 import IntegrityError

from sqlalchemy.orm import sessionmaker

from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowGeneration
from masterone.mdm.workflow_service import conflict

VERSION = 'p119_workflow_final_v2'


def source_context(db, run, generation):
    from masterone.mdm.workflow_generation_reads import GenerationReader
    reader = GenerationReader(db, run, generation)
    reader._ensure_source()
    context = reader._source_artifact.payload.get('metadata', {}).get('workflow_context')
    if not isinstance(context, dict):
        raise ValueError('WORKFLOW_ARTIFACT_INVALID')
    return context


def finalize_generation(db, run, generation, *, actor):
    from masterone.mdm.export_stream import index
    from masterone.mdm.golden_service import require_resolved_choices, selected_golden, validate_selections
    from masterone.mdm.models import ResolutionTask
    from masterone.mdm.workflow_generation_reads import GenerationReader
    from masterone.mdm.workflow_snapshot_storage import seal_snapshot
    reader = GenerationReader(db, run, generation)
    counts = reader.coverage()
    if not counts['clusters'] or reader.unconfirmed_count():
        conflict('GOLDEN_CONFIRMATIONS_REQUIRED')
    if counts['unresolved_records']:
        conflict('GOLDEN_COVERAGE_INVALID')
    task = db.query(ResolutionTask).filter_by(run_id=run.id).one()
    from masterone.mdm.workflow_models import WorkflowOperation
    op = db.get(WorkflowOperation, task.operation_id)
    if op is None or op.run_id != run.id or op.actor_user_id != actor or op.operation_type != 'FINALIZE_GOLDEN':
        raise ValueError('WORKFLOW_OPERATION_IDENTITY')
    context = source_context(db, run, generation)
    run_id, gid, revision, golden_revision = run.id, generation.id, generation.revision, run.golden_revision
    operation_id = task.operation_id
    source_id, source_hash = generation.coverage['source_artifact_id'], generation.coverage['source_hash']
    factory = sessionmaker(db.get_bind(), expire_on_commit=False)
    metadata = {'generation_id': gid, 'cluster_revision': revision, 'golden_revision': golden_revision,
                'source_artifact_id': source_id, 'source_hash': source_hash, 'coverage': counts,
                'master_id_version': 'workflow_entity_v1', 'policy_version': context['policy_version'],
                'source_context_hash': digest(context)}
    db.rollback()  # Source iteration, Parquet IO and upload hold no run lock.
    from masterone.mdm.workflow_snapshot_producers import validate_streamed_snapshot
    validate_streamed_snapshot(db, run, context)
    with index(get_settings()) as (state, budget, _directory):
        state.executescript('CREATE TABLE expected(id TEXT PRIMARY KEY); CREATE TABLE covered(id TEXT PRIMARY KEY); '
                            'CREATE TABLE entities(id TEXT PRIMARY KEY);')
        for record in reader.records():
            state.execute('INSERT INTO expected VALUES (?)', (record['id'],))
        written = 0
        def groups():
            nonlocal written
            for row in reader.rows():
                if not row.confirmed:
                    conflict('GOLDEN_CONFIRMATIONS_REQUIRED')
                require_resolved_choices(row)
                validate_selections(db, run, row, row.selections)
                try:
                    state.executemany('INSERT INTO covered VALUES (?)', ((rid,) for rid in row.payload['member_record_ids']))
                except Exception as error:
                    import sqlite3
                    if isinstance(error, sqlite3.IntegrityError):
                        conflict('GOLDEN_COVERAGE_INVALID')
                    raise
                if not row.payload.get('entity_id'):
                    conflict('WORKFLOW_ENTITY_IDENTITY_INVALID')
                try:
                    state.execute('INSERT INTO entities VALUES (?)', (row.payload['entity_id'],))
                except IntegrityError:
                    conflict('WORKFLOW_ENTITY_IDENTITY_INVALID')
                yield {key: row.payload[key] for key in ('id', 'entity_id', 'member_record_ids', 'singleton', 'status')} | {
                    'golden_fields': selected_golden(db, row, reader), 'selections': row.selections,
                    'identity_version': 1}
                written += 1
                if written % 512 == 0:
                    budget.check()
            if (written != counts['clusters'] or state.execute('SELECT COUNT(*) FROM covered').fetchone()[0] != counts['total_records']
                    or state.execute('SELECT id FROM expected EXCEPT SELECT id FROM covered LIMIT 1').fetchone()
                    or state.execute('SELECT id FROM covered EXCEPT SELECT id FROM expected LIMIT 1').fetchone()):
                conflict('GOLDEN_COVERAGE_INVALID')
        artifact_id = seal_snapshot(factory, run_id, 'FINAL_GOLDEN', metadata, {'groups': groups()}, operation_id=operation_id)
        budget.check()
    return {'version': VERSION, **metadata, 'artifact_id': artifact_id,
            'artifact_hash': _artifact_hash(db, artifact_id), 'run_id': run_id}


def _artifact_hash(db, artifact_id):
    from masterone.mdm.workflow_models import WorkflowArtifact
    return db.query(WorkflowArtifact.content_hash).filter_by(id=artifact_id).scalar()


class FinalizedView:
    def __init__(self, db, run, generation, source=None, groups=None):
        self.db, self.run, self.generation = db, run, generation
        self.receipt = generation.finalized_result
        self.source, self.groups = source, groups
        self.result_hash = generation.result_hash
        self.coverage = self.receipt.get('coverage', {})
        self.master_id_version = self.receipt.get('master_id_version', 'workflow_cluster_v1')

    def iter_groups(self):
        if self.groups is not None:
            yield from self.groups.iter_rows('groups')
        else:
            yield from self.receipt['clusters']

    def record(self, record_id):
        if self.source is not None:
            try:
                return self.source.records[record_id]
            except KeyError:
                conflict('FINAL_RESULT_INVALID')
        return self.receipt['records'][record_id]

    def record_ids(self):
        if self.source is not None:
            yield from self.source.records
        else:
            yield from self.receipt['records']


@contextmanager
def open_finalized_generation(db, run, *, state=None, budget=None):
    from masterone.mdm.workflow_cluster_reads import current_generation
    from masterone.mdm.workflow_models import WorkflowArtifact
    from masterone.mdm.workflow_snapshot_storage import open_snapshot
    generation = current_generation(db, run)
    if state is not None:
        version = db.query(WorkflowGeneration.finalized_result['version']).filter_by(id=generation.id).scalar()
        if version != VERSION:
            from masterone.mdm.workflow_export_spool import spool_result
            yield spool_result(db, generation.id, generation.result_hash, run.golden_revision, state, budget)
            return
    result = generation.finalized_result
    if (not result or digest(result) != generation.result_hash or result.get('golden_revision') != run.golden_revision):
        conflict('FINAL_RESULT_INVALID')
    if result.get('version') != VERSION:
        yield FinalizedView(db, run, generation)
        return
    artifact = db.get(WorkflowArtifact, result['artifact_id'])
    if (result.get('run_id') != run.id or result.get('generation_id') != generation.id or artifact is None
            or artifact.content_hash != result.get('artifact_hash')):
        conflict('FINAL_RESULT_INVALID')
    try:
        with open_snapshot(db, artifact.id, run.id, 'FINAL_GOLDEN') as groups:
            for key in ('generation_id', 'cluster_revision', 'golden_revision', 'source_artifact_id', 'source_hash', 'coverage'):
                if groups.get(key) != result.get(key):
                    conflict('FINAL_RESULT_INVALID')
            with open_snapshot(db, result['source_artifact_id'], run.id, 'SINGLETON_SOURCE') as source:
                if source.row.content_hash != result['source_hash']:
                    conflict('FINAL_RESULT_INVALID')
                yield FinalizedView(db, run, generation, source, groups)
    except (ValueError, OSError):
        conflict('FINAL_RESULT_INVALID')


def run_finalization(operation_id, factory, progress):
    from masterone.mdm.workflow_cluster_reads import current_generation
    from masterone.mdm.workflow_jobs import finish, job_identity
    from masterone.mdm.workflow_service import audit
    with factory.begin() as db:
        run, op, _task = job_identity(db, operation_id)
        expected = (run.workflow_revision, run.cluster_revision, run.golden_revision, run.decision_revision)
        generation = current_generation(db, run)
        if (op.result.get('generation_id') != generation.id
                or op.result.get('expected_cluster_revision') != run.cluster_revision
                or op.result.get('expected_golden_revision') != run.golden_revision
                or op.result.get('expected_decision_revision') != run.decision_revision
                or op.result.get('source_hash') != generation.coverage.get('source_hash')):
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        generation_id = generation.id
        actor = op.actor_user_id
    progress('finalize-golden', 0, 1)
    with factory() as db:
        run, op, _task = job_identity(db, operation_id)
        receipt = finalize_generation(db, run, db.get(WorkflowGeneration, generation_id), actor=actor)
    with factory() as db:
        from masterone.mdm.models import ResolutionRun
        from masterone.mdm.workflow_snapshot_producers import validate_streamed_snapshot
        run = db.get(ResolutionRun, receipt['run_id'])
        generation = db.get(WorkflowGeneration, generation_id)
        context = source_context(db, run, generation)
        if digest(context) != receipt['source_context_hash'] or generation.coverage['source_hash'] != receipt['source_hash']:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        validate_streamed_snapshot(db, run, context)
    with factory.begin() as db:
        run, op, task = job_identity(db, operation_id)
        if (run.workflow_revision, run.cluster_revision, run.golden_revision, run.decision_revision) != expected:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        if current_generation(db, run).id != generation_id:
            raise ValueError('WORKFLOW_REVISION_CHANGED')
        generation = db.get(WorkflowGeneration, generation_id)
        generation.finalized_result, generation.result_hash = receipt, digest(receipt)
        finish(db, run, op, task, 'FINALIZED')
        op.result = {'workflow_state': 'FINALIZED', 'workflow_revision': run.workflow_revision,
                     'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision,
                     'result_hash': generation.result_hash, 'streamed_finalization': True}
        audit(db, run, actor, 'WORKFLOW_FINALIZE_GOLDEN', op.result)
