"""Bounded, actionable conflict evidence without altering decisions."""
from sqlalchemy import or_

from masterone.mdm.data_profile import record_read
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.workflow_models import WorkflowConflict, WorkflowPair
from masterone.mdm.workflow_pair_reads import pair_detail
from masterone.mdm.workflow_review import paginate


def conflict_page(db, run, limit, cursor):
    def serialize(row):
        ids = set(row.payload.get('record_ids', []))
        primary = db.query(WorkflowPair).filter_by(run_id=run.id, id=row.pair_id).one_or_none()
        if primary:
            ids.update(primary.baseline[key] for key in ('left_id', 'right_id'))
        records = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id,
            ResolutionRecord.id.in_(ids)).order_by(ResolutionRecord.id).limit(100).all()
        query = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id, or_(
            WorkflowPair.id == row.pair_id,
            WorkflowPair.baseline['left_id'].as_string().in_(ids) &
            WorkflowPair.baseline['right_id'].as_string().in_(ids)))
        pairs = query.order_by((WorkflowPair.id != row.pair_id), WorkflowPair.id).limit(25).all()
        return {'conflict_id': row.id, 'pair_id': row.pair_id,
            'reason': row.payload.get('reason', 'CLUSTER_CONFLICT'), 'record_ids': sorted(ids),
            'records': [record_read(record) for record in records],
            'pairs': [pair_detail(db, run, pair.id) for pair in pairs],
            'related_pair_count': query.count(), 'record_count': len(ids)}
    return paginate(db.query(WorkflowConflict).filter_by(run_id=run.id, resolved=False),
                    WorkflowConflict.id, limit, cursor, serialize)
