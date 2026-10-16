"""Compare feedback persistence paths in isolated SQLite; never connects to QA DB."""
# ruff: noqa: E402 -- standalone script imports the checkout's backend after path setup
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))

from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.base import Base
from masterone.mdm.matching_foundation_models import StewardFeedback
from masterone.mdm.models import ResolutionAuditEvent, ResolutionEngineSelection, ResolutionRun
from masterone.mdm.steward_feedback import capture_review_snapshot, persist_feedback, prepare_feedback_batch
from masterone.mdm.workflow_models import WorkflowOperation, WorkflowPair
from masterone.mdm.workflow_review import apply_decision
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def measure(batched, count):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(id='actor', email='offline@example.test', email_normalized='offline@example.test',
                    password_hash='unused-offline-benchmark', role='STEWARD'))
        db.add(ResolutionRun(id='run', owner_user_id='actor', workflow_version=2))
        db.flush()
        db.add(ResolutionEngineSelection(run_id='run', engine_version='p119_current', configuration={}))
        db.add_all(WorkflowPair(id=f'p{i:06}', run_id='run', baseline={
            'id': f'p{i:06}', 'left_id': 'a', 'right_id': 'b', 'score': .5, 'field_evidence': []})
            for i in range(count))
        db.add(WorkflowOperation(id='op', run_id='run', request_id='benchmark', operation_type='BULK_DECISIONS',
            actor_user_id='actor', payload_hash='0' * 64, payload={'decision': 'no_merge'}))
    statements, sql_seconds, began = Counter(), 0., 0.
    def before(conn, cursor, statement, parameters, context, executemany):
        nonlocal began
        statements[statement.split()[0].upper()] += 1
        began = time.perf_counter()
    def after(*_):
        nonlocal sql_seconds
        sql_seconds += time.perf_counter() - began
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    started = time.perf_counter()
    with factory.begin() as db:
        run, op = db.get(ResolutionRun, 'run'), db.get(WorkflowOperation, 'op')
        rows = db.query(WorkflowPair).order_by(WorkflowPair.id).all()
        batch = prepare_feedback_batch(db, run, rows, op, 'actor') if batched else None
        with db.no_autoflush:
            for pair in rows:
                snapshot = capture_review_snapshot(db, run, pair, batch=batch)
                apply_decision(db, run, pair, 'no_merge', 'actor', links=set(), resolve_conflicts=False)
                persist_feedback(db, run, pair, op, 'actor', snapshot, 'HUMAN_BULK_DIRECT', batch=batch)
        db.flush()
    seconds = time.perf_counter() - started
    event.remove(engine, 'before_cursor_execute', before)
    event.remove(engine, 'after_cursor_execute', after)
    with factory() as db:
        assert db.query(StewardFeedback).count() == count
        assert db.query(ResolutionAuditEvent).count() == 2 * count
        assert all(row.human_decision == 'REJECTED' and row.human_direct_label is None
                   for row in db.query(StewardFeedback))
    engine.dispose()
    return {'pairs': count, 'seconds': seconds, 'sql_seconds': sql_seconds,
            'statement_count': dict(statements), 'feedback_rows': count, 'audit_rows': 2 * count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    previous = settings.steward_feedback_capture_enabled
    settings.steward_feedback_capture_enabled = True
    try:
        report = {'scope': 'SQLite in-memory, feedback + decisions + audit only; not HTTP/PostgreSQL/Golden',
                  'before': measure(False, 100), 'after': measure(True, 100)}
    finally:
        settings.steward_feedback_capture_enabled = previous
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
