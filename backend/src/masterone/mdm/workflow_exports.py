"""Adapter for the existing preview/export/history transport, using server results."""
import csv
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.generation_staging import digest, lock_run
from masterone.mdm.models import ResolutionExport, ResolutionPreview
from masterone.mdm.workflow_cluster_reads import current_generation
from masterone.mdm.workflow_service import audit, conflict


def final_result(db, run, choices=None):
    if choices:
        raise HTTPException(422, {'code': 'SERVER_GOLDEN_CHOICES_REQUIRED'})
    if run.workflow_state != 'FINALIZED':
        conflict('WORKFLOW_NOT_FINALIZED')
    generation = current_generation(db, run)
    result = generation.finalized_result
    if not result or digest(result) != generation.result_hash or result['golden_revision'] != run.golden_revision:
        conflict('FINAL_RESULT_INVALID')
    return generation


def readiness(db, run_id, choices=None):
    run = lock_run(db, run_id)
    generation = final_result(db, run, choices)
    counts = final_counts(generation.finalized_result)
    return {'can_export': True, 'pending_review_count': 0,
            'merge_count': counts['multi_record_groups'],
            'separate_count': counts['singletons'],
            'missing_choices': [], 'blocked_proposal_ids': [], 'result_hash': generation.result_hash}


def prepare_preview(db, run_id, actor, choices, acknowledge):
    run = lock_run(db, run_id)
    generation = final_result(db, run, choices)
    counts = final_counts(generation.finalized_result)
    preview = ResolutionPreview(run_id=run_id, actor_user_id=actor, policy_version=run.policy_version,
        pair_set_hash=generation.result_hash, proposal_revisions={'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision},
        choices={}, acknowledge_unreviewed=False, expires_at=now_utc() + timedelta(minutes=15))
    db.add(preview)
    db.flush()
    audit(db, run, actor, 'WORKFLOW_PREVIEW_CREATED', {'preview_id': preview.id, 'result_hash': generation.result_hash})
    response = {'preview_id': preview.id, 'status': 'OPEN', 'eligible_count': counts['multi_record_groups'],
        'review_required_count': 0, 'separate_record_count': counts['singletons'],
        'estimated_output_rows': counts['clusters'], 'policy_version': run.policy_version, 'expires_at': preview.expires_at,
        'result_hash': generation.result_hash}
    db.commit()
    return response


def final_counts(result):
    if result.get('version') == 'p119_workflow_final_v2':
        return result['coverage']
    clusters = result['clusters']
    singles = sum(len(c['member_record_ids']) == 1 for c in clusters)
    return {'clusters': len(clusters), 'singletons': singles, 'multi_record_groups': len(clusters)-singles,
            'covered_records': len(result['records']), 'total_records': len(result['records'])}


def field_value(field, data):
    if field in ('phone', 'email'):
        from masterone.mdm.pipeline.survivorship_policy import primary_values
        return '; '.join(str(v['value']) for v in primary_values(field, data))
    return data.get('value') or ''


def master_record_id(cluster, result):
    if result.get('master_id_version') == 'workflow_entity_v1':
        if not cluster.get('entity_id'):
            raise ValueError('WORKFLOW_ENTITY_IDENTITY_INVALID')
        return cluster['entity_id']
    return cluster['id']


def create_export(db, run_id, actor, acknowledge, choices=None, *, commit=True, export_id=None):
    from masterone.mdm.export_stream import COLUMNS, digest_chunks, file_chunks, index
    from masterone.storage.object_store import LocalObjectStore
    from masterone.storage.r2 import get_store
    settings = get_settings()
    run = lock_run(db, run_id)
    if choices:
        raise HTTPException(422, {'code': 'SERVER_GOLDEN_CHOICES_REQUIRED'})
    if export_id and (prior := db.get(ResolutionExport, export_id)):
        if prior.run_id != run_id or prior.actor_user_id != actor:
            conflict('EXPORT_IDENTITY_CONFLICT')
        return prior
    generation = final_result(db, run, choices)
    result, result_hash = generation.finalized_result, generation.result_hash
    cluster_revision, golden_revision = run.cluster_revision, run.golden_revision
    export_id = export_id or str(uuid4())
    db.rollback()  # File/upload/verification never hold the run lock.
    from masterone.mdm.workflow_finalization import open_finalized_generation
    with index(settings) as (state, budget, directory), open_finalized_generation(db, run) as final:
        path = directory / 'snapshot.csv'
        state.executescript('CREATE TABLE covered(id TEXT PRIMARY KEY); CREATE TABLE expected(id TEXT PRIMARY KEY);')
        state.executemany('INSERT INTO expected VALUES (?)', ((rid,) for rid in final.record_ids()))
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for cluster in final.iter_groups():
                members = cluster['member_record_ids']
                try:
                    state.executemany('INSERT INTO covered VALUES (?)', ((rid,) for rid in members))
                except Exception as error:
                    import sqlite3
                    if isinstance(error, sqlite3.IntegrityError):
                        conflict('EXPORT_COVERAGE_INVALID')
                    raise
                fields = cluster['golden_fields'].get('fields', {})
                sources = [final.record(rid) for rid in members]
                writer.writerow(dict(master_record_id=master_record_id(cluster, result),
                    full_name=field_value('name', fields.get('name', {})),
                    phone_number=field_value('phone', fields.get('phone', {})),
                    email_address=field_value('email', fields.get('email', {})),
                    raw_address=field_value('address', fields.get('address', {})),
                    source_record_ids=';'.join(members), source_systems=';'.join(sorted({r['source_system'] for r in sources})),
                    record_count=len(members), resolution_status='UNMERGED' if len(members) == 1 else 'MERGED', review_pending='false'))
                budget.check()
        if (state.execute('SELECT id FROM expected EXCEPT SELECT id FROM covered LIMIT 1').fetchone()
                or state.execute('SELECT id FROM covered EXCEPT SELECT id FROM expected LIMIT 1').fetchone()):
            conflict('EXPORT_COVERAGE_INVALID')
        checksum, size = digest_chunks(file_chunks(path, budget))
        key = f'exports/{run_id}/{export_id}/{checksum}.csv'
        store = get_store(settings)
        local = store is None
        store = store or LocalObjectStore(settings.export_artifact_dir)
        store.upload_file(path, key, 'text/csv; charset=utf-8')
        remote_hash, remote_size = digest_chunks(store.iter_chunks(key))
        if remote_hash != checksum or remote_size != size:
            conflict('EXPORT_ARTIFACT_INVALID')
        budget.check()
    run = lock_run(db, run_id)
    current = final_result(db, run)
    if current.result_hash != result_hash or run.cluster_revision != cluster_revision or run.golden_revision != golden_revision:
        conflict('STALE_EXPORT_REVISION')
    if prior := db.get(ResolutionExport, export_id):
        if prior.run_id != run_id or prior.actor_user_id != actor:
            conflict('EXPORT_IDENTITY_CONFLICT')
        return prior
    counts = final_counts(result)
    snapshot = ResolutionExport(id=export_id, run_id=run_id, actor_user_id=actor,
        r2_key=('local:' if local else '') + key, csv_bytes=None,
        row_count=counts['clusters'], merged_group_count=counts['multi_record_groups'], pending_review_count=0)
    db.add(snapshot)
    audit(db, run, actor, 'CSV_EXPORTED', {'export_id': export_id, 'result_hash': result_hash,
        'master_id_version': result.get('master_id_version', 'workflow_cluster_v1'),
        'cluster_revision': cluster_revision, 'golden_revision': golden_revision,
        'csv_sha256': checksum, 'byte_size': size, 'metrics': budget.metrics})
    db.flush()
    if commit:
        db.commit()
    return snapshot


def execute_preview(db, run_id, preview_id, request_id, actor):
    run = lock_run(db, run_id)
    preview = db.get(ResolutionPreview, preview_id, populate_existing=True)
    if preview is None or preview.run_id != run_id or preview.actor_user_id != actor:
        raise HTTPException(404, 'Preview not found')
    if preview.status == 'EXECUTED':
        if preview.execute_request_id != request_id:
            conflict('PREVIEW_ALREADY_EXECUTED')
        return db.get(ResolutionExport, preview.export_id)
    generation = final_result(db, run)
    if preview.expires_at.replace(tzinfo=None) <= now_utc().replace(tzinfo=None) or preview.pair_set_hash != generation.result_hash:
        conflict('STALE_PREVIEW')
    snapshot = create_export(db, run_id, actor, False, {}, commit=False, export_id=preview_id)
    preview = db.get(ResolutionPreview, preview_id, populate_existing=True)
    if preview.status == 'EXECUTED' and preview.execute_request_id != request_id:
        conflict('PREVIEW_ALREADY_EXECUTED')
    preview.status, preview.execute_request_id, preview.export_id = 'EXECUTED', request_id, snapshot.id
    audit(db, db.get(type(run), run_id), actor, 'PREVIEW_EXECUTED', {'preview_id': preview_id, 'export_id': snapshot.id})
    db.commit()
    return snapshot
