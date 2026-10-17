"""Bounded Golden list responses without hydrating member alternatives or evidence."""
import unicodedata
from types import SimpleNamespace

from masterone.mdm.golden_service import selected_golden
from masterone.mdm.workflow_cluster_reads import current_generation, generation_metadata
from masterone.mdm.workflow_generation_reads import GenerationReader
from masterone.mdm.workflow_models import WorkflowCluster


def search_text(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value or '').casefold().replace('đ', 'd'))
                   if unicodedata.category(c) != 'Mn')


def index_rows(db, reader, cursor=None, status='all', finalized=False):
    virtual_matches = status == 'all' or (status == 'confirmed') == (finalized or reader._default_confirmed())
    if reader.virtual and virtual_matches:
        yield from reader.rows({'after': cursor})
        return
    query = db.query(WorkflowCluster.id, WorkflowCluster.confirmed, WorkflowCluster.golden,
        WorkflowCluster.selections, WorkflowCluster.payload['member_record_ids'],
        WorkflowCluster.payload['display_name'], WorkflowCluster.payload['requires_manual_review'],
        WorkflowCluster.payload['pending_selections']).filter_by(generation_id=reader.generation.id)
    if cursor is not None:
        query = query.filter(WorkflowCluster.id > cursor)
    if status != 'all' and not finalized:
        query = query.filter(WorkflowCluster.confirmed.is_(status == 'confirmed'))
    for cid, confirmed, golden, selections, members, name, manual, pending in query.order_by(WorkflowCluster.id).yield_per(256):
        yield SimpleNamespace(id=cid, confirmed=confirmed, golden=golden, selections=selections,
            generation_id=reader.generation.id, payload={'member_record_ids': members or [],
            'display_name': name, 'requires_manual_review': manual or [], 'pending_selections': pending or {}})


def golden_index(db, run, limit=25, cursor=None, status='all', q=''):
    generation = current_generation(db, run, allow_stale=True)
    reader = GenerationReader(db, run, generation)
    metadata = generation_metadata(db, run, generation)
    total = metadata['clusters']
    missing = reader.unconfirmed_count()
    summary = {'total': total, 'confirmed': total - missing, 'needs_review': missing}
    items, matched, has_next = [], 0, False
    term = search_text(q).strip()
    finalized = run.workflow_state == 'FINALIZED'
    known_total = summary['total' if status == 'all' else 'needs_review' if status == 'review' else 'confirmed']
    rows = index_rows(db, reader, None if term else cursor, status, finalized) if known_total else ()
    for row in rows:
        confirmed = run.workflow_state == 'FINALIZED' or row.confirmed
        if status == 'review' and confirmed or status == 'confirmed' and not confirmed:
            continue
        if not term and len(items) >= limit:
            has_next = True
            break
        fields = selected_golden(db, row, reader).get('fields', {})
        name = fields.get('name', {}).get('value') or row.payload.get('display_name') or row.id
        phone, email = fields.get('phone', {}).get('value'), fields.get('email', {}).get('value')
        if term and term not in search_text(f'{name} {phone or ""} {email or ""} {row.id}'):
            continue
        matched += 1
        if cursor is not None and row.id <= cursor:
            continue
        if len(items) >= limit:
            has_next = True
            continue
        items.append({'cluster_id': row.id, 'display_name': name, 'member_count': len(row.payload['member_record_ids']),
            'phone': phone, 'email': email, 'status': 'FINALIZED' if run.workflow_state == 'FINALIZED' else 'CONFIRMED' if confirmed else 'DRAFT',
            'review_fields': sorted(set(row.payload.get('requires_manual_review', [])) | set(row.payload.get('pending_selections', {})))})
    return {'items': items, 'total': matched if term else known_total, 'next_cursor': items[-1]['cluster_id'] if has_next and items else None,
            'summary': summary, 'generation': metadata}
