"""Prepared synthetic scenarios, isolated from steward feedback and normal runs."""
from masterone.auth.models import DemoGrant
from masterone.mdm.demo_dataset import VERSION, SOURCES
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair

REQUEST_ID = 'demo-preset:customer-demo-v1'
REASON = 'Tình huống demo được chuẩn bị sẵn từ dữ liệu giả lập; không phải quyết định của người dùng.'


def prepare_examples(db, run):
    """Caller holds the run lock. Use existing safety checks and real publication jobs.

    Only eight known synthetic identities are preset. All other pairs remain
    reviewable. A receipt prevents reapplying examples after a judge changes them.
    """
    from masterone.mdm.workflow_assessment_reads import active_assessment
    from masterone.mdm.workflow_service import active_operation, audit, new_operation
    from masterone.mdm.workflow_review import eligibility, negative_links
    from masterone.mdm.workflow_cluster_queue import schedule_cluster_refresh
    grant = db.get(DemoGrant, run.owner_user_id)
    if not grant or grant.state != 'READY' or grant.run_id != run.id or grant.dataset_version != VERSION:
        return {'status': 'NOT_APPLICABLE', 'sample_groups': 0}
    receipt = db.query(WorkflowOperation).filter_by(run_id=run.id, request_id=REQUEST_ID).one_or_none()
    child = db.get(WorkflowOperation, receipt.result.get('cluster_refresh_operation_id')) if receipt else None
    if receipt and child and child.status == 'COMPLETED' and run.cluster_revision > receipt.result.get('base_cluster_revision', 0):
        return {'status': 'READY', 'sample_groups': receipt.result.get('sample_groups', 0)}
    if active_operation(db, run.id):
        return {'status': 'PREPARING' if receipt else 'WAITING',
                'sample_groups': receipt.result.get('sample_groups', 0) if receipt else 0}
    if receipt:
        # A failed publication is retried explicitly; never overwrite later reviews.
        return {'status': 'FAILED' if child and child.status in ('FAILED', 'RETRYABLE_FAILED', 'CANCELLED') else 'PREPARING',
                'sample_groups': receipt.result.get('sample_groups', 0)}
    if not active_assessment(db, run.id):
        return {'status': 'WAITING', 'sample_groups': 0}
    groups, pairs = [], []
    # Indices 20..27 deliberately avoid the dataset's different-person examples.
    for number in range(21, 29):
        records = db.query(ResolutionRecord).filter(ResolutionRecord.run_id == run.id,
            ResolutionRecord.external_id.in_([f'{source}-{number:04d}' for _, source, _ in SOURCES])).all()
        if len(records) != 4 or {r.source_system for r in records} != {s[1] for s in SOURCES}:
            continue
        ids = {record.id for record in records}
        candidates = db.query(WorkflowPair).filter(WorkflowPair.run_id == run.id,
            WorkflowPair.baseline['left_id'].as_string().in_(ids),
            WorkflowPair.baseline['right_id'].as_string().in_(ids)).all()
        # A judge's decision anywhere inside this fixture takes precedence.
        if not candidates or any(p.decision != 'UNDECIDED' or p.revision != 0 for p in candidates):
            continue
        links = negative_links(db, run.id, candidates)
        eligible = [p for p in candidates if eligibility(p)['manual_allowed'] and
                    tuple(sorted((p.baseline['left_id'], p.baseline['right_id']))) not in links]
        if links:
            continue
        connected = {records[0].id}
        for _ in range(4):
            for pair in eligible:
                ends = {pair.baseline['left_id'], pair.baseline['right_id']}
                if connected & ends:
                    connected.update(ends)
        if connected != ids:
            continue
        groups.append(sorted(ids))
        pairs.extend(eligible)
    receipt = new_operation(db, run, 'DEMO_PRESET', REQUEST_ID,
        {'origin': 'DEMO_PRESET', 'dataset_version': VERSION}, run.owner_user_id)
    receipt.status, receipt.stage = 'COMPLETED', 'completed'
    receipt.result = {'origin': 'DEMO_PRESET', 'base_cluster_revision': run.cluster_revision,
                      'sample_groups': len(groups), 'record_groups': groups,
                      'pair_ids': [pair.id for pair in pairs]}
    for pair in pairs:
        # No human-feedback writer: prepared examples must not train a profile.
        pair.decision, pair.review_reason, pair.automation = 'ACCEPTED', REASON, None
        pair.revision += 1
    if pairs:
        run.decision_revision += 1
        run.workflow_revision += 1
    audit(db, run, run.owner_user_id, 'DEMO_EXAMPLES_PREPARED', receipt.result)
    db.flush()
    schedule_cluster_refresh(db, run, receipt)
    return {'status': 'PREPARING', 'sample_groups': len(groups)}
