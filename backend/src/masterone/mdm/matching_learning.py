"""Fail-closed M3 readiness, without training on TEST or fabricating splits."""
from collections import Counter

from masterone.mdm.generation_staging import digest
from masterone.mdm.matching_foundation_models import LabeledDataset, StewardFeedback
from masterone.mdm.matching_foundation_service import latest_binding, owned
from masterone.mdm.ownership import owned_run


def learning_status(db, run_id, actor, dataset_id=None):
    owned_run(db, run_id, actor)
    dataset = owned(db, LabeledDataset, dataset_id, actor) if dataset_id else None
    query = db.query(StewardFeedback).filter_by(run_id=run_id, owner_user_id=actor)
    total = query.count()
    if total > 10000:
        return {'status': 'BLOCKED', 'reasons': ['LABEL_SNAPSHOT_BUDGET_EXCEEDED'], 'counts': None,
                'total_feedback_events': total, 'snapshot_event_cap': 10000, 'profile_created': False,
                'automation_allowed': False, 'training_performed': False, 'thresholds_changed': False}
    rows = query.order_by(StewardFeedback.id).all()
    from masterone.mdm.workflow_models import WorkflowPair
    pair_ids = {r.workflow_pair_id for r in rows}
    pairs = {p.id: (p.revision, p.decision) for p in db.query(WorkflowPair).filter(
             WorkflowPair.run_id == run_id, WorkflowPair.id.in_(pair_ids))} if pair_ids else {}
    superseded = {row.supersedes_feedback_id for row in rows if row.supersedes_feedback_id}
    current = [row for row in rows if row.id not in superseded and row.origin in {'HUMAN_DIRECT', 'HUMAN_BULK_DIRECT'}
               and pairs.get(row.workflow_pair_id) == (row.pair_revision, row.human_decision)
               and row.trainability_status == 'TRAINABLE' and row.human_direct_label in {'MATCH', 'NON_MATCH'}
               and digest(row.feature_snapshot_json) == row.feature_snapshot_hash]
    counts = Counter(row.human_direct_label for row in current)
    snapshot_hash = digest([{'id': r.id, 'pair_id': r.workflow_pair_id, 'revision': r.pair_revision,
                            'label': r.human_direct_label, 'feature_hash': r.feature_snapshot_hash} for r in current])
    reasons = []
    if not counts['MATCH'] or not counts['NON_MATCH']:
        reasons.append('BOTH_LABEL_CLASSES_REQUIRED')
    if dataset is None or dataset.truth_verification_status != 'VERIFIED' or dataset.trainability_status != 'TRAINABLE':
        reasons.append('INDEPENDENT_LABEL_PROVENANCE_NOT_VERIFIED')
    invalid_split = bool(dataset and dataset.truth_verification_status == 'VERIFIED'
                         and dataset.trainability_status == 'TRAINABLE'
                         and (not dataset.split_manifest_hash or not dataset.split_manifest_uri))
    if invalid_split:
        reasons.append('INDEPENDENT_TRAIN_DEV_TEST_SPLIT_REQUIRED')
    if not reasons:
        # Registry metadata cannot prove byte-level split or leakage validation.
        # A trainer/objective must be approved and implemented before release.
        reasons.append('SPLIT_CONTENT_AND_LEARNING_OBJECTIVE_NOT_VALIDATED')
        invalid_split = True
    binding = latest_binding(db, run_id)
    return {'status': 'INVALID_SPLIT' if invalid_split else 'INSUFFICIENT_LABELS', 'reasons': reasons,
            'counts': {'MATCH': counts['MATCH'], 'NON_MATCH': counts['NON_MATCH']},
            'feedback_snapshot_hash': snapshot_hash, 'effective_feedback_count': len(current),
            'superseded_feedback_count': len(superseded), 'profile_created': False, 'automation_allowed': False,
            'current_profile_id': binding.profile_id if binding else None,
            'current_profile_digest': binding.profile_digest if binding else None,
            'training_performed': False, 'thresholds_changed': False,
            'test_used_for_training': False, 'learning_implementation_status': 'DATA_GATED_NOT_QUALITY_CERTIFIED'}
