"""Authorized reassessment of the specified TC4 run; aggregate audit only, no decisions changed."""
import argparse
import json
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text

from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.generation_staging import digest
from masterone.mdm.models import ResolutionAuditEvent, ResolutionNegativeEdge, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_models import WorkflowPair

RUN_ID = '3449137c-8f4d-41cc-9152-fa12768aa355'
OUT = Path('/app/data/work/pair-assessment-live')


def fingerprints(db):
    if db.get_bind().dialect.name == 'postgresql':
        # Hash immutable JSON on the database; do not transfer ~80k evidence documents for an audit.
        hashes = db.execute(text("SELECT md5(string_agg(md5(id || baseline::text), '' ORDER BY id)), "
            "md5(string_agg(md5(id || decision || revision::text), '' ORDER BY id)) "
            "FROM mdm_workflow_pairs WHERE run_id=:run"), {'run':RUN_ID}).one()
        counts = dict(db.execute(text('SELECT decision,count(*) FROM mdm_workflow_pairs WHERE run_id=:run GROUP BY decision'),{'run':RUN_ID}).all())
        edges = sorted((e.id,e.left_record_id,e.right_record_id,e.active) for e in db.query(ResolutionNegativeEdge).filter_by(run_id=RUN_ID))
        return {'baseline_hash':hashes[0], 'decisions_hash':hashes[1], 'negative_edges_hash':digest(edges),
            'negative_edge_count':len(edges),'decisions':counts,
            'audit_ids':sorted(e.id for e in db.query(ResolutionAuditEvent).filter_by(run_id=RUN_ID))}
    baseline, decisions = sha256(), sha256()
    counts = {}
    for pair in db.query(WorkflowPair).filter_by(run_id=RUN_ID).order_by(WorkflowPair.id).yield_per(500):
        baseline.update(digest([pair.id, pair.baseline]).encode())
        decisions.update(digest([pair.id, pair.decision, pair.revision]).encode())
        counts[pair.decision] = counts.get(pair.decision, 0) + 1
    edges = sorted((e.id, e.left_record_id, e.right_record_id, e.active) for e in
                   db.query(ResolutionNegativeEdge).filter_by(run_id=RUN_ID))
    audit_ids = sorted(e.id for e in db.query(ResolutionAuditEvent).filter_by(run_id=RUN_ID))
    return {'baseline_hash': baseline.hexdigest(), 'decisions_hash': decisions.hexdigest(),
            'negative_edges_hash': digest(edges), 'negative_edge_count': len(edges), 'decisions': counts,
            'audit_ids': audit_ids}


def main(mode):
    OUT.mkdir(parents=True, exist_ok=True)
    engine, factory = create_database(get_settings().active_mdm_database_url)
    try:
        with factory() as db:
            run = db.get(ResolutionRun, RUN_ID)
            task = db.query(ResolutionTask).filter_by(run_id=RUN_ID).one_or_none()
            print(json.dumps({'state': run.workflow_state, 'workflow_revision': run.workflow_revision,
                'decision_revision': run.decision_revision, 'task_status': task.status if task else None}))
            if mode == 'capture':
                if task and task.status in {'RUNNING','QUEUED'}:
                    raise ValueError('WAIT_FOR_EXISTING_OPERATION')
                before = fingerprints(db)
                before['decision_revision'] = run.decision_revision
                (OUT / 'before.json').write_text(json.dumps(before), encoding='utf-8')
                print('Captured baseline, decisions, cannot-link and audit history fingerprints.')
            elif mode in {'queue','advice'}:
                from masterone.mdm.workflow_assessment_reads import active_assessment
                from masterone.mdm.workflow_service import begin_operation
                assessment = active_assessment(db, RUN_ID)
                payload = {'request_id': f'pair-assessment-live-{mode}-{uuid4()}',
                    'expected_workflow_revision': run.workflow_revision,
                    'expected_assessment_id': assessment.id if assessment else None}
                if mode == 'queue':
                    payload['model_scope'] = 'synthetic-benchmark'
                else:
                    payload.update(pair_ids=None, limit=3)  # bounded paid provider canary
                result = begin_operation(db, RUN_ID, 'ASSESS_PAIRS' if mode == 'queue' else 'PAIR_ADVICE',
                    payload['request_id'], payload, run.workflow_revision, run.owner_user_id)
                (OUT / f'{mode}-operation.json').write_text(json.dumps(result), encoding='utf-8')
                print(json.dumps(result))
            elif mode == 'verify':
                from masterone.mdm.workflow_pair_reads import pair_summary
                before = json.loads((OUT / 'before.json').read_text())
                after = fingerprints(db)
                checks = {key: before[key] == after[key] for key in ('baseline_hash','decisions_hash','negative_edges_hash')}
                checks['history_preserved'] = set(before['audit_ids']) <= set(after['audit_ids'])
                checks['decision_revision_unchanged'] = before['decision_revision'] == run.decision_revision
                if not all(checks.values()):
                    raise ValueError('LIVE_HISTORY_FINGERPRINT_CHANGED')
                summary = pair_summary(db, run)
                checks['no_unclassified'] = 'UNCLASSIFIED' not in {g['key'] for g in summary['groups']}
                checks['all_pairs_assessed'] = db.query(WorkflowPair).filter_by(run_id=RUN_ID,assessment_id=summary['assessment_id']).count() == summary['total']
                report = {'checks':checks, 'summary':summary, 'decisions':after['decisions'],
                    'negative_edge_count':after['negative_edge_count'], 'audit_count':len(after['audit_ids'])}
                (OUT / 'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False))
                assert all(checks.values())
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['capture','queue','verify','advice'])
    main(parser.parse_args().mode)
