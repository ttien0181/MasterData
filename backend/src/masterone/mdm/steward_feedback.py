"""Explicit human-origin events; capture before amendments alter assessment."""
import math
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, or_, tuple_

from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.mdm.generation_staging import digest
from masterone.mdm.matching_foundation_models import MatchingProfile, StewardFeedback
from masterone.mdm.matching_foundation_service import (
    FEATURE_SCHEMA_ID,
    SCOPE,
    authorize,
    governance_audit,
    latest_binding,
)
from masterone.mdm.models import ResolutionEngineSelection
from masterone.mdm.workflow_assessment_reads import effective_result


@dataclass
class _FeedbackBatch:
    session: object
    transaction: object
    run_id: str
    operation_id: str
    actor: str
    decision: str
    revisions: dict
    binding: object
    profile_identity: str | None
    engine_identity: str | None
    existing: dict
    previous: dict

    def check(self, db, run, pair, op=None, actor=None):
        if (db is not self.session or db.get_transaction() is not self.transaction or run.id != self.run_id
                or pair.id not in self.revisions or pair.run_id != run.id
                or (op is not None and (op.id != self.operation_id or actor != self.actor))):
            raise ValueError('FEEDBACK_BATCH_IDENTITY_CHANGED')


def prepare_feedback_batch(db, run, pairs, op, actor):
    """Transaction-local prefetch; never cache permissions across commits.

    The actor row remains locked until the batch commits. Reads and inserts
    retain pair revision identity, supersession and one audit per decision.
    """
    if not get_settings().steward_feedback_capture_enabled:
        return None
    from masterone.mdm.workflow_review import DECISIONS
    if op.operation_type != 'BULK_DECISIONS' or op.run_id != run.id or op.actor_user_id != actor:
        raise ValueError('FEEDBACK_OPERATION_NOT_HUMAN_DIRECT')
    decision = DECISIONS[op.payload['decision']]
    authorize(db, actor, 'approve_pair' if decision == 'ACCEPTED' else 'reject_pair', owner=run.owner_user_id)
    binding = latest_binding(db, run.id)
    profile = db.get(MatchingProfile, binding.profile_id) if binding else None
    selection = db.get(ResolutionEngineSelection, run.id)
    pair_ids = [pair.id for pair in pairs]
    latest = db.query(StewardFeedback.workflow_pair_id.label('pid'),
        func.max(StewardFeedback.pair_revision).label('revision')).filter(
            StewardFeedback.run_id == run.id, StewardFeedback.workflow_pair_id.in_(pair_ids)).group_by(
                StewardFeedback.workflow_pair_id).subquery()
    # Fetch only each pair's latest row and its target revision, even if an
    # old pair has a long amendment history.
    rows = db.query(StewardFeedback).outerjoin(latest, StewardFeedback.workflow_pair_id == latest.c.pid).filter(
        StewardFeedback.run_id == run.id, StewardFeedback.workflow_pair_id.in_(pair_ids),
        or_(StewardFeedback.pair_revision == latest.c.revision,
            tuple_(StewardFeedback.workflow_pair_id, StewardFeedback.pair_revision).in_(
                [(pair.id, pair.revision + 1) for pair in pairs]))).all()
    previous = {}
    for row in rows:
        if row.workflow_pair_id not in previous or row.pair_revision > previous[row.workflow_pair_id].pair_revision:
            previous[row.workflow_pair_id] = row
    return _FeedbackBatch(db, db.get_transaction(), run.id, op.id, actor, decision,
        {pair.id: pair.revision for pair in pairs}, binding, profile.config_digest if profile else None,
        digest(selection.configuration) if selection else None,
        {(row.workflow_pair_id, row.pair_revision): row for row in rows}, previous)


def capture_review_snapshot(db, run, pair, *, batch=None):
    if not get_settings().steward_feedback_capture_enabled:
        return None
    result = effective_result(pair)
    score = result.get('score')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError('FEEDBACK_FEATURE_SCORE_INVALID')
    fields = []
    for item in result.get('field_evidence', result.get('evidence', [])):
        similarity = item.get('similarity')
        if isinstance(similarity, bool) or not isinstance(similarity, (int, float)) or not math.isfinite(similarity):
            raise ValueError('FEEDBACK_FEATURE_SIMILARITY_INVALID')
        fields.append({key: deepcopy(item[key]) for key in
            ('field', 'similarity', 'state', 'normalization_tags', 'matched_frequency') if key in item})
    if batch is not None:
        batch.check(db, run, pair)
        if pair.revision != batch.revisions[pair.id]:
            raise ValueError('FEEDBACK_BATCH_REVISION_CHANGED')
        binding, profile_identity, engine_identity = batch.binding, batch.profile_identity, batch.engine_identity
    else:
        binding = latest_binding(db, run.id)
        selection = db.get(ResolutionEngineSelection, run.id)
        profile = db.get(MatchingProfile, binding.profile_id) if binding else None
        profile_identity = profile.config_digest if profile else None
        engine_identity = digest(selection.configuration) if selection else None
    negative_derived = ('STEWARD_NEGATIVE_DECISION' in result.get('constraint_codes', []) or
                        result.get('reason') == 'STEWARD_NEGATIVE_DECISION')
    return {'features': {'rule_score': float(score), 'fields': fields, 'score_unit': 'p119_rule_v1'},
        'assessment_id': pair.assessment_id, 'pair_revision_before': pair.revision,
        'reviewed_at': now_utc(), 'binding': binding,
        'profile_identity': profile_identity,
        'negative_derived': negative_derived,
        'evidence_references': {'pair_id': pair.id, 'assessment_id': pair.assessment_id,
            'legacy_engine_identity': engine_identity,
            'source': 'DISPLAYED_EFFECTIVE_ASSESSMENT_BEFORE_REVIEW',
            'feature_schema_identity': FEATURE_SCHEMA_ID}}


def persist_feedback(db, run, pair, op, actor, snapshot, origin, *, batch=None):
    if snapshot is None:
        return None
    if origin not in {'HUMAN_DIRECT', 'HUMAN_BULK_DIRECT'}:
        raise ValueError('FEEDBACK_NOT_HUMAN_DIRECT')
    allowed_operations = {'HUMAN_DIRECT': {'PAIR_DECISION', 'AMEND_PAIR'},
                          'HUMAN_BULK_DIRECT': {'BULK_DECISIONS'}}
    if op.operation_type not in allowed_operations[origin]:
        raise ValueError('FEEDBACK_OPERATION_NOT_HUMAN_DIRECT')
    if batch is None:
        authorize(db, actor, 'approve_pair' if pair.decision == 'ACCEPTED' else 'reject_pair', owner=run.owner_user_id)
    else:
        batch.check(db, run, pair, op, actor)
        if origin != 'HUMAN_BULK_DIRECT' or pair.decision != batch.decision:
            raise ValueError('FEEDBACK_BATCH_DECISION_CHANGED')
    if op.run_id != run.id or op.actor_user_id != actor or pair.revision != snapshot['pair_revision_before'] + 1:
        raise ValueError('FEEDBACK_DECISION_IDENTITY_CHANGED')
    existing = batch.existing.get((pair.id, pair.revision)) if batch else db.query(StewardFeedback).filter_by(
        workflow_pair_id=pair.id, pair_revision=pair.revision).one_or_none()
    if existing:
        if existing.operation_id != op.id or existing.human_decision != pair.decision:
            raise ValueError('FEEDBACK_REVISION_CONFLICT')
        return existing
    previous = batch.previous.get(pair.id) if batch else db.query(StewardFeedback).filter_by(
        workflow_pair_id=pair.id).order_by(StewardFeedback.pair_revision.desc()).first()
    label = {'ACCEPTED': 'MATCH', 'NON_MATCH': 'NON_MATCH'}.get(pair.decision)
    binding = snapshot['binding']
    trainable = label is not None and not snapshot['negative_derived']
    row = StewardFeedback(id=str(uuid4()), owner_user_id=run.owner_user_id, creator_id=actor,
        data_scope=binding.data_scope if binding else SCOPE, run_id=run.id, workflow_pair_id=pair.id,
        pair_revision=pair.revision, operation_id=op.id, actor_id=actor,
        reviewed_at=snapshot['reviewed_at'], origin=origin, human_decision=pair.decision,
        human_direct_label=label, supersedes_feedback_id=previous.id if previous else None,
        feature_snapshot_json=snapshot['features'], feature_snapshot_hash=digest(snapshot['features']),
        evidence_references=snapshot['evidence_references'], assessment_id=snapshot['assessment_id'],
        binding_id=binding.id if binding else None, profile_id=binding.profile_id if binding else None,
        profile_digest=snapshot['profile_identity'], feature_schema_identity=FEATURE_SCHEMA_ID,
        trainability_status='TRAINABLE' if trainable else 'NOT_TRAINABLE',
        trainability_reason='HUMAN_DIRECT_SNAPSHOT' if trainable else
            'HITL_DERIVED_FEATURES' if snapshot['negative_derived'] else 'NO_BINARY_LABEL')
    db.add(row)
    if batch is None:
        db.flush()
    else:
        batch.existing[pair.id, pair.revision] = row
        batch.previous[pair.id] = row
    governance_audit(db, actor, row.data_scope, 'FEEDBACK_SUPERSEDED' if previous else 'FEEDBACK_CAPTURED',
        'StewardFeedback', row.id, run_id=run.id, pair_id=pair.id,
        details={'feedback_id': row.id, 'pair_revision': pair.revision, 'origin': origin,
                 'supersedes_feedback_id': row.supersedes_feedback_id, 'feature_hash': row.feature_snapshot_hash})
    return row
