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
    clusters = generation.finalized_result['clusters']
    return {'can_export': True, 'pending_review_count': 0,
            'merge_count': sum(len(c['member_record_ids']) > 1 for c in clusters),
            'separate_count': sum(len(c['member_record_ids']) == 1 for c in clusters),
            'missing_choices': [], 'blocked_proposal_ids': [], 'result_hash': generation.result_hash}


def prepare_preview(db, run_id, actor, choices, acknowledge):
    run = lock_run(db, run_id)
    generation = final_result(db, run, choices)
    clusters = generation.finalized_result['clusters']
    preview = ResolutionPreview(run_id=run_id, actor_user_id=actor, policy_version=run.policy_version,
        pair_set_hash=generation.result_hash, proposal_revisions={'cluster_revision': run.cluster_revision, 'golden_revision': run.golden_revision},
        choices={}, acknowledge_unreviewed=False, expires_at=now_utc() + timedelta(minutes=15))
    db.add(preview)
    db.flush()
    audit(db, run, actor, 'WORKFLOW_PREVIEW_CREATED', {'preview_id': preview.id, 'result_hash': generation.result_hash})
    response = {'preview_id': preview.id, 'status': 'OPEN', 'eligible_count': sum(len(c['member_record_ids']) > 1 for c in clusters),
        'review_required_count': 0, 'separate_record_count': sum(len(c['member_record_ids']) == 1 for c in clusters),
        'estimated_output_rows': len(clusters), 'policy_version': run.policy_version, 'expires_at': preview.expires_at,
        'result_hash': generation.result_hash}
    db.commit()
    return response


def field_value(field, data):
    if field in ('phone', 'email') and data.get('values'):
        return '; '.join(dict.fromkeys(str(v['value']) for v in data['values'] if v.get('value')))
    return data.get('value') or ''


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
    with index(settings) as (_state, budget, directory):
        path = directory / 'snapshot.csv'
        covered = []
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for cluster in result['clusters']:
                members = cluster['member_record_ids']
                covered.extend(members)
                fields = cluster['golden_fields'].get('fields', {})
                sources = [result['records'][rid] for rid in members]
                writer.writerow(dict(master_record_id=cluster['id'],
                    full_name=field_value('name', fields.get('name', {})),
                    phone_number=field_value('phone', fields.get('phone', {})),
                    email_address=field_value('email', fields.get('email', {})),
                    raw_address=field_value('address', fields.get('address', {})),
                    source_record_ids=';'.join(members), source_systems=';'.join(sorted({r['source_system'] for r in sources})),
                    record_count=len(members), resolution_status='UNMERGED' if len(members) == 1 else 'MERGED', review_pending='false'))
                budget.check()
        if len(covered) != len(set(covered)) or set(covered) != set(result['records']):
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
    snapshot = ResolutionExport(id=export_id, run_id=run_id, actor_user_id=actor,
        r2_key=('local:' if local else '') + key, csv_bytes=None,
        row_count=len(result['clusters']), merged_group_count=sum(len(c['member_record_ids']) > 1 for c in result['clusters']), pending_review_count=0)
    db.add(snapshot)
    audit(db, run, actor, 'CSV_EXPORTED', {'export_id': export_id, 'result_hash': result_hash,
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
