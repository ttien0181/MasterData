"""QA PostgreSQL checks in a new, uniquely owned schema; never truncate shared QA."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

os.environ['MASTERONE_ENV_FILE'] = '.env.qa'
os.environ['SESSION_WORKFLOW_ENABLED'] = 'true'

from alembic import command
from fastapi import HTTPException
from masterone.config import get_settings
from masterone.mdm.generation_staging import digest
from masterone.mdm.job_queue import OwnershipLostError, claim_next, ownership
from masterone.mdm.migrate import HEAD, alembic_config
from masterone.mdm.models import ResolutionJobAttempt, ResolutionRun, ResolutionTask
from masterone.mdm.workflow_contracts import GoldenRequest
from masterone.mdm.workflow_jobs import execute_workflow
from masterone.mdm.workflow_models import WorkflowArtifact, WorkflowCluster, WorkflowGeneration, WorkflowOperation
from masterone.mdm.workflow_service import begin_operation
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

OUT = Path('artifacts/session-workflow-2sessions/20261012/postgres.json')


def main():
    schema = 'session_workflow_qa_' + uuid4().hex
    assert re.fullmatch(r'session_workflow_qa_[a-f0-9]{32}', schema)
    url = make_url(get_settings().active_mdm_database_url)
    assert url.drivername.startswith('postgresql')
    base = create_engine(url, connect_args={'connect_timeout': 10})
    report = {'schema': schema, 'checks': [], 'migration_head': HEAD}
    engine = None
    try:
        with base.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={'connect_timeout': 10})

        @event.listens_for(engine, 'begin')
        def namespace(connection):
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')

        def migrate(target):
            with engine.begin() as connection:
                cfg = alembic_config(url.render_as_string(hide_password=False))
                cfg.attributes['connection'] = connection
                command.upgrade(cfg, target)

        migrate('0027_llm_policy')
        with engine.begin() as db:
            db.execute(text("INSERT INTO users (id,email,email_normalized,password_hash,role,is_active,created_at) VALUES ('owner','owner@example.test','owner@example.test','unused','STEWARD',true,CURRENT_TIMESTAMP)"))
            db.execute(text("INSERT INTO mdm_resolution_runs (id,owner_user_id,status,phase,policy_version,record_count,file_count,candidate_count,insufficient_count,created_at) VALUES ('legacy','owner','COMPLETED','completed','person-v3',0,0,0,0,CURRENT_TIMESTAMP)"))
        migrate('head')
        with engine.connect() as db:
            assert db.execute(text("SELECT workflow_version, workflow_revision, status FROM mdm_resolution_runs WHERE id='legacy'")).one() == (1, 0, 'COMPLETED')
            assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == HEAD
        report['checks'].append('postgres_additive_migration_preserves_legacy')
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            db.add(ResolutionRun(id='race', owner_user_id='owner', workflow_version=2, workflow_state='DATA_READY'))
        barrier = Barrier(2)

        def start(i):
            with factory() as db:
                db.get(ResolutionRun, 'race')  # Deliberately populate the pre-lock identity map.
                barrier.wait(timeout=10)
                payload = {'request_id': f'tab-{i}', 'expected_workflow_revision': 0}
                try:
                    return begin_operation(db, 'race', 'DECIDE', payload['request_id'], payload, 0, 'owner')['operation_id']
                except HTTPException as e:
                    assert e.status_code == 409
                    return '409'

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, (1, 2)))
        assert results.count('409') == 1
        with factory() as db:
            assert db.query(WorkflowOperation).filter_by(run_id='race', operation_type='DECIDE').count() == 1
        report['checks'].append('postgres_two_tabs_one_enqueue_stale_409')

        with factory.begin() as db:
            task = db.query(ResolutionTask).filter_by(run_id='race').one()
            task.status = 'COMPLETED'
            db.get(WorkflowOperation, task.operation_id).status = 'COMPLETED'
            db.add(ResolutionRun(id='fence', owner_user_id='owner', workflow_version=2, workflow_state='DATA_PREPARING'))
        with factory() as db:
            payload = {'request_id': 'prepare', 'expected_workflow_revision': 0}
            operation = begin_operation(db, 'fence', 'PREPARE', 'prepare', payload, 0, 'owner')
        claim = claim_next(factory, 'postgres-expired', lease_seconds=30)
        with factory.begin() as db:
            db.get(ResolutionJobAttempt, claim.attempt_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with ownership(claim):
            try:
                execute_workflow(operation['operation_id'], factory, lambda *_: None)
                raise AssertionError('expired owner published')
            except OwnershipLostError:
                pass
        report['checks'].append('postgres_expired_worker_cannot_publish')

        with factory.begin() as db:
            db.add(ResolutionRun(id='golden-race', owner_user_id='owner', workflow_version=2, workflow_state='GOLDEN_REVIEW', cluster_revision=1))
            db.flush()
            artifact = WorkflowArtifact(run_id='golden-race', phase='CLUSTER', payload={}, content_hash=digest({}))
            db.add(artifact)
            db.flush()
            generation = WorkflowGeneration(run_id='golden-race', revision=1, artifact_id=artifact.id)
            db.add(generation)
            db.flush()
            db.add(WorkflowCluster(id='cluster', generation_id=generation.id,
                payload={'member_record_ids': [], 'status': 'SINGLETON', 'singleton': True}, golden={'fields': {}}))
        barrier = Barrier(2)

        def edit(i):
            from masterone.mdm.golden_service import golden_command
            with factory() as db:
                db.get(ResolutionRun, 'golden-race')
                barrier.wait(timeout=10)
                body = GoldenRequest(request_id=f'golden-{i}', expected_workflow_revision=0,
                    expected_cluster_revision=1, expected_golden_revision=0,
                    selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': f'Name {i}'}})
                try:
                    golden_command(db, 'golden-race', body, 'owner', 'SAVE_GOLDEN', 'cluster')
                    return '200'
                except HTTPException as e:
                    assert e.status_code == 409
                    return '409'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, (1, 2)))
        assert sorted(results) == ['200', '409']
        report['checks'].append('postgres_two_golden_writers_one_success_stale_409')
        report['status'] = 'PASS'
    except Exception as error:
        report['status'], report['error_type'] = 'FAIL', type(error).__name__
        if getattr(error, 'orig', None) is not None:
            report['sqlstate'] = getattr(error.orig, 'sqlstate', None)
    finally:
        if engine:
            engine.dispose()
        with base.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()
        report['cleanup'] = 'owned_schema_only'
        OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
