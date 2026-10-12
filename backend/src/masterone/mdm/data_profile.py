from collections import Counter

from fastapi import HTTPException

from masterone.mdm.models import ResolutionFile, ResolutionRecord

PROFILE_FIELDS = ('name', 'phone', 'email', 'address', 'dob', 'government_id')


def build_profile(rows):
    flags, quality, sources, present = Counter(), Counter(), Counter(), Counter()
    count = flagged = 0
    schema = set()
    for row in rows:
        count += 1
        row_flags = set(row.get('quality_flags') or [])
        flagged += bool(row_flags)
        flags.update(row_flags)
        sources.update([row['source_system']])
        raw = row.get('raw_fields') or {}
        schema.update(raw)
        present.update(field for field in PROFILE_FIELDS
                       if raw.get(field) is not None and str(raw[field]).strip())
        quality.update(f'{field}:{value}' for field, value in
                       (row.get('normalized_fields') or {}).get('_field_quality', {}).items())
    return {'record_count': count, 'unit': 'records', 'quality_flags': dict(flags),
            'field_quality': dict(quality), 'source_counts': dict(sources),
            'records_with_quality_flags': flagged, 'schema': sorted(schema),
            'field_completeness': {field: {'present': present[field], 'missing': count - present[field]}
                                   for field in PROFILE_FIELDS}}


def record_read(row):
    return {'record_id': row.id, 'source_system': row.source_system, 'external_id': row.external_id,
            'raw_fields': row.raw_fields, 'normalized_fields': row.normalized_fields,
            'quality_flags': row.quality_flags, 'extra_fields': row.extra_fields}


def data_read(db, run_id, limit, cursor, offset=None):
    from masterone.mdm.workflow_artifacts import load
    from masterone.mdm.workflow_models import WorkflowArtifact
    from masterone.mdm.workflow_review import paginate

    query = db.query(ResolutionRecord).filter_by(run_id=run_id)
    if cursor and offset is None:
        page = paginate(query, ResolutionRecord.id, limit, cursor, record_read)
        page['offset'] = query.filter(ResolutionRecord.id <= cursor).count()
    else:
        offset = offset or 0
        total = query.count()
        rows = query.order_by(ResolutionRecord.id).offset(offset).limit(limit + 1).all()
        page = {'items': [record_read(row) for row in rows[:limit]], 'total': total, 'offset': offset,
                'next_cursor': rows[limit - 1].id if len(rows) > limit else None}
    files = db.query(ResolutionFile).filter_by(run_id=run_id).order_by(ResolutionFile.filename).all()
    # Avoid transferring the full input snapshot to render ten rows.
    artifact = db.query(WorkflowArtifact.id, WorkflowArtifact.content_hash).filter_by(
        run_id=run_id, phase='PREPARE').order_by(WorkflowArtifact.created_at.desc()).first()
    profile = None
    if artifact:
        try:
            summary = db.query(WorkflowArtifact).filter_by(run_id=run_id, phase='PROFILE').filter(
                WorkflowArtifact.payload['prepare_artifact_id'].as_string() == artifact.id
            ).order_by(WorkflowArtifact.created_at.desc()).first()
            if summary:
                payload = load(db, summary.id, run_id, 'PROFILE')
                if payload['prepare_content_hash'] != artifact.content_hash:
                    raise ValueError('WORKFLOW_ARTIFACT_INVALID')
            else:
                payload = load(db, artifact.id, run_id, 'PREPARE')
        except ValueError as error:
            raise HTTPException(409, detail={'code': 'WORKFLOW_ARTIFACT_INVALID'}) from error
        profile = payload['profile']
        if not all(key in profile for key in ('field_completeness', 'records_with_quality_flags', 'schema')):
            # Derive missing metrics without changing the sealed snapshot or rerunning the engine.
            records = payload.get('records')
            if records is None:
                records = (record_read(row) for row in query.yield_per(512))
            profile = {**profile, **build_profile(records)}
    return {'files': [{'filename': f.filename, 'row_count': f.row_count, 'sha256': f.sha256} for f in files],
            'schema': profile['schema'] if profile else [], 'profile': profile, 'samples': page}
