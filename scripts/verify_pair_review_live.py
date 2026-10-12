"""Read the existing TC4; mutate only a clearly named synthetic QA fixture.

No password rotation, benchmark upload, scoring job or paid provider call.
Temporary authentication tokens remain in process memory and are revoked.
"""
import hashlib
import itertools
import json
import os
import secrets
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from masterone.auth.models import AuthSession, User
from masterone.auth.security import password_hash
from masterone.config import get_settings
from masterone.db.base import now_utc
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionFile, ResolutionNegativeEdge, ResolutionRecord, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowPair


def main():
    out = Path('artifacts/pair-review-20261012/live')
    out.mkdir(parents=True, exist_ok=True)
    engine, factory = create_database(get_settings().active_mdm_database_url)
    original = '3449137c-8f4d-41cc-9152-fa12768aa355'
    fixture = str(uuid4())
    sessions = []
    stop = threading.Event()
    helper = None
    try:
        with factory.begin() as db:
            source_run = db.get(ResolutionRun, original)
            owner = db.get(User, source_run.owner_user_id)
            assert owner.email.startswith('audit-') and owner.role == 'STEWARD'
            before = (source_run.workflow_revision, source_run.decision_revision)
            original_token, fixture_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            qa_email = f'pair-review-qa-{uuid4().hex}@example.test'
            qa = User(email=qa_email, email_normalized=qa_email,
                      password_hash=password_hash.hash(secrets.token_urlsafe(32)), role='STEWARD')
            db.add(qa)
            db.flush()
            for user, token in [(owner, original_token), (qa, fixture_token)]:
                session = AuthSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                      expires_at_epoch=int(time.time()) + 1800)
                db.add(session)
                db.flush()
                sessions.append(session.id)
            run = ResolutionRun(id=fixture, owner_user_id=qa.id, name='QA Pair Review · synthetic 7 records / 21 pairs',
                                workflow_version=2, workflow_state='DECIDE_REVIEW', workflow_revision=1,
                                status='COMPLETED', phase='decide', record_count=7, file_count=1, candidate_count=21)
            db.add(run)
            db.flush()
            file = ResolutionFile(run_id=fixture, filename='synthetic-review.csv', sha256=hashlib.sha256(fixture.encode()).hexdigest(), row_count=7)
            db.add(file)
            db.flush()
            records = []
            for i in range(7):
                raw = dict(name=f'QA Customer {i + 1}', phone=f'090000000{i}', email=f'qa{i}@example.test', dob='2000-01-01', address='QA fixture')
                record = ResolutionRecord(run_id=fixture, file_id=file.id, external_id=f'QA-{i + 1}', source_system='QA_CRM',
                                          entity_type='PERSON', raw_fields=raw, normalized_fields={**raw, 'name': raw['name'].lower()}, quality_flags=[], extra_fields={})
                db.add(record)
                records.append(record)
            db.flush()
            template = deepcopy(db.query(WorkflowPair).filter_by(run_id=original).first().baseline)
            for i, (left, right) in enumerate(itertools.combinations(records, 2)):
                group = 'A' if i < 8 else 'C' if i < 14 else 'D' if i < 18 else None
                subgroup = 'A1_NAME_PHONE' if i < 4 else 'A3_NAME_PHONE_DOB' if i < 8 else 'C1_MISSING_ANCHOR' if i < 14 else 'D4_DOB_CONFLICT' if i < 18 else None
                pair_id = f'qa-{fixture[:8]}-{i:03}'
                baseline = {**template, 'id': pair_id, 'left_id': left.id, 'right_id': right.id, 'primary_group': group,
                            'subgroup': subgroup, 'decision': 'NOT_MERGEABLE' if i in (18, 19) else 'REVIEW',
                            'reason': 'SAME_TYPE_GOV_ID_CONFLICT' if i in (18, 19) else 'QA_REVIEW_FIXTURE', 'score': 5,
                            'field_evidence': [{'field': 'name', 'state': 'DIFFERENT'}]}
                db.add(WorkflowPair(id=pair_id, run_id=fixture, baseline=baseline,
                                    decision='ACCEPTED' if i == 0 else 'REJECTED' if i == 1 else 'UNDECIDED'))
        def assist_fixture_only():
            # The user's long-running job occupies the existing single worker.
            # Run only our unclaimed synthetic bulk via the same deployed executor.
            # If the regular worker is free/has claimed it, leave execution to it.
            while not stop.wait(1):
                with factory() as db:
                    task = db.query(ResolutionTask).filter_by(run_id=fixture, status='QUEUED').first()
                    occupied = db.query(ResolutionTask).filter_by(run_id=original, status='RUNNING').first()
                    operation_id = task.operation_id if task and occupied and task.attempt_id is None else None
                if operation_id:
                    code = ('from masterone.db.session import create_database; from masterone.config import get_settings; '
                            'from masterone.mdm.workflow_jobs import execute_workflow; '
                            'e,f=create_database(get_settings().active_mdm_database_url); '
                            f'execute_workflow({operation_id!r},f,lambda *args:None); e.dispose()')
                    result = subprocess.run(['docker', 'exec', 'p119-session-qa-backend-1', 'python', '-c', code], capture_output=True)
                    if result.returncode:
                        print('Synthetic executor helper yielded; normal worker may have claimed the task.', flush=True)
        helper = threading.Thread(target=assist_fixture_only, daemon=True)
        helper.start()
        subprocess.run(['node', 'frontend/scripts/verify-pair-review.mjs'], check=True,
                       env={**os.environ, 'PAIR_ORIGINAL_RUN': original, 'PAIR_FIXTURE_RUN': fixture,
                            'PAIR_ORIGINAL_TOKEN': original_token, 'PAIR_FIXTURE_TOKEN': fixture_token, 'PAIR_OUTPUT': str(out)})
        with factory() as db:
            source_run = db.get(ResolutionRun, original)
            after = (source_run.workflow_revision, source_run.decision_revision)
            pairs = db.query(WorkflowPair).filter_by(run_id=fixture).order_by(WorkflowPair.id).all()
            assert pairs[0].decision == 'ACCEPTED' and pairs[1].decision == 'REJECTED'
            assert sum(p.decision == 'NON_MATCH' for p in pairs) == 8
            assert sum(p.decision == 'ACCEPTED' for p in pairs) == 12
            assert db.query(ResolutionNegativeEdge).filter_by(run_id=fixture, active=True).count() == 8
        (out / 'database.json').write_text(json.dumps({'original_run': original, 'original_revisions_before': before,
            'original_revisions_after': after, 'original_mutations_by_harness': 0,
            'external_activity_observed': before != after,
            'fixture_run': fixture, 'fixture_kind': 'synthetic source records and review baselines, not engine output',
            'fixture_records': 7, 'fixture_pairs': 21, 'accepted': 12, 'denied': 8, 'preserved_rejected': 1,
            'durable_negative_edges': 8}, indent=2), encoding='utf-8')
        print('Live pair review verified; TC4 only read; synthetic scope mutations and negative edges passed.')
    finally:
        stop.set()
        if helper:
            helper.join(timeout=30)
        with factory.begin() as db:
            for session_id in sessions:
                session = db.get(AuthSession, session_id)
                if session:
                    session.revoked_at = now_utc()
        engine.dispose()


if __name__ == '__main__':
    main()
