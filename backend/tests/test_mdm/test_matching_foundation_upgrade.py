"""Populated pre-M1 PostgreSQL upgrade must preserve decisions and audit."""
import json
from uuid import uuid4

from masterone.auth.models import User
from masterone.mdm.matching_foundation_models import MatchingBinding, StewardFeedback
from masterone.mdm.models import ResolutionAuditEvent
from masterone.mdm.ownership import owned_run
from masterone.mdm.workflow_models import WorkflowPair
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.test_mdm.test_matching_foundation_postgresql import migrate, pg_engine  # noqa: F401


def test_pg_populated_upgrade_preserves_decision_audit_and_owner(request):
    engine = request.getfixturevalue('pg_engine')
    migrate(engine, '0032_generation_coverage')
    actor, run_id, pair_id, audit_id = [str(uuid4()) for _ in range(4)]
    baseline = {'id': pair_id, 'left_id': 'legacy-left', 'right_id': 'legacy-right',
                'score': 9.0, 'decision': 'MERGEABLE', 'primary_group': 'A',
                'triage_status': 'REVIEW', 'field_evidence': []}
    with engine.begin() as db:
        db.execute(text("INSERT INTO users (id,email,email_normalized,password_hash,is_active,role,created_at) VALUES (:id,'old-m1@example.com','old-m1@example.com','qa-not-a-login',true,'STEWARD',CURRENT_TIMESTAMP)"), {'id': actor})
        db.execute(text("INSERT INTO mdm_resolution_runs (id,owner_user_id,status,phase,policy_version,record_count,file_count,candidate_count,insufficient_count,created_at,workflow_version,workflow_state,workflow_revision,decision_revision) VALUES (:id,:owner,'COMPLETED','completed','person-v1',2,1,1,0,CURRENT_TIMESTAMP,2,'DECIDE_REVIEW',8,7)"), {'id': run_id, 'owner': actor})
        db.execute(text("INSERT INTO mdm_workflow_pairs (id,run_id,baseline,decision,revision) VALUES (:id,:run,CAST(:baseline AS json),'REJECTED',7)"), {'id': pair_id, 'run': run_id, 'baseline': json.dumps(baseline)})
        db.execute(text("INSERT INTO mdm_resolution_audit_events (id,run_id,actor_user_id,action,details,created_at) VALUES (:id,:run,:actor,'WORKFLOW_PAIR_REJECTED',CAST(:details AS json),CURRENT_TIMESTAMP)"), {'id': audit_id, 'run': run_id, 'actor': actor, 'details': json.dumps({'pair_id': pair_id, 'pair_revision': 7})})
    migrate(engine, 'head')
    factory = sessionmaker(engine)
    with factory() as db:
        run = owned_run(db, run_id, actor)
        assert (run.workflow_revision, run.decision_revision, run.owner_user_id) == (8, 7, actor)
        pair = db.get(WorkflowPair, pair_id)
        assert pair.baseline == baseline and pair.decision == 'REJECTED' and pair.revision == 7
        event = db.get(ResolutionAuditEvent, audit_id)
        assert event.details == {'pair_id': pair_id, 'pair_revision': 7}
        assert event.owner_user_id is None and event.run_id == run_id
        assert db.query(MatchingBinding).count() == 0 and db.query(StewardFeedback).count() == 0
        assert db.get(User, actor).role == 'STEWARD'
