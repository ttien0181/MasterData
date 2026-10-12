"""Verify one available <=10k benchmark TC against the running API and worker.

Credentials stay in process memory; reports contain counts and timings only.
Run with PYTHONPATH=backend/src;backend and MASTERONE_ENV_FILE=.env.qa.
"""
import csv
import io
import json
import os
import secrets
import subprocess
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx
import pyarrow.parquet as pq
from masterone.auth.models import User
from masterone.auth.security import password_hash
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.data_profile import PROFILE_FIELDS, data_read
from masterone.mdm.models import ResolutionRecord, ResolutionRun
from masterone.mdm.workflow_models import WorkflowArtifact
from sqlalchemy import func

OUT = Path('artifacts/data-overview-20261012')
DATASETS = Path('C:/Users/nguye/Downloads/benchmark_datasets-20261011T132408Z-1-003/benchmark_datasets')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = secrets.choice(sorted(DATASETS.glob('*/10k/seed-7/pipeline-output/prepare/records.parquet')))
    parquet = pq.ParquetFile(source)
    columns = [name for name in parquet.schema_arrow.names if name.startswith('raw__')]
    names = [name[5:] for name in columns]
    assert not {'entity_id', 'truth', 'cluster_id'} & set(names)
    rows = []
    for batch in parquet.iter_batches(batch_size=1000, columns=columns):
        rows.extend({name[5:]: row[name] for name in columns} for row in batch.to_pylist())
        if len(rows) >= 10000:
            rows = rows[:10000]
            break
    assert 0 < len(rows) <= 10000
    files = []
    for start in range(0, len(rows), 5000):
        text = io.StringIO(newline='')
        writer = csv.DictWriter(text, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows[start:start + 5000])
        files.append(('files', (f'overview-{start // 5000 + 1}.csv', text.getvalue().encode(), 'text/csv')))
    engine, factory = create_database(get_settings().active_mdm_database_url)
    email, password = f'overview-{uuid4().hex}@example.com', secrets.token_urlsafe(32)
    report = {'dataset': source.relative_to(DATASETS).parts[0], 'size': '10k', 'used_rows': len(rows),
              'available_complete_prepare_tcs': len(list(DATASETS.glob('*/10k/seed-7/pipeline-output/prepare/records.parquet'))),
              'input_projection': 'raw fields only; capped at 10000; no truth labels', 'checks': []}
    with httpx.Client(base_url='http://localhost:8001/api/v1', headers={'Origin': 'http://localhost:3000'}, timeout=180) as client:
        def call(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code != expected:
                raise RuntimeError(f'{method} {path} HTTP {response.status_code}')
            return response.json()

        existing_run = os.environ.get('OVERVIEW_EXISTING_RUN')
        if existing_run:
            # Resume only this harness's account; no second upload or benchmark TC.
            with factory.begin() as db:
                owner = db.get(User, db.get(ResolutionRun, existing_run).owner_user_id)
                assert owner.email.startswith('overview-') and owner.role == 'STEWARD'
                email = owner.email
                owner.password_hash = password_hash.hash(password)
            report['upload_reused'] = True
        else:
            call('POST', '/auth/register', 201, json={'email': email, 'password': password})
            with factory.begin() as db:
                db.query(User).filter_by(email_normalized=email).one().role = 'STEWARD'
        login = call('POST', '/auth/login', json={'email': email, 'password': password})
        client.headers['X-CSRF-Token'] = login['csrf_token']
        if existing_run and os.environ.get('OVERVIEW_BROWSER_ONLY') == '1':
            subprocess.run(['node', 'frontend/scripts/verify-data-overview.mjs'],
                           env={**os.environ, 'OVERVIEW_EMAIL': email, 'OVERVIEW_PASSWORD': password,
                                'OVERVIEW_RUN': existing_run}, check=True)
            engine.dispose()
            print('Existing session browser verification passed', flush=True)
            return
        started = time.monotonic()
        run = existing_run or call('POST', '/mdm/runs', 201, data={'workflow_version': '2'}, files=files)['run_id']
        report['run_id'] = run
        print('Uploaded one TC:', report['dataset'], len(rows), 'records; preparing', flush=True)
        url = f'/mdm/runs/{run}/workflow'
        while True:
            state = call('GET', url)
            if state['retryable_operation']:
                raise RuntimeError('PREPARE worker failure')
            if state['workflow_state'] == 'DATA_READY':
                break
            if time.monotonic() - started > 600:
                raise TimeoutError('PREPARE timeout')
            time.sleep(2)
        report['ready_check_seconds' if existing_run else 'prepare_seconds'] = round(time.monotonic() - started, 3)
        pages = []
        for offset in (0, 10, len(rows) - 10):
            started = time.monotonic()
            page = call('GET', url + f'/data?offset={offset}&limit=10')
            pages.append(page)
            report.setdefault('page_seconds', []).append(round(time.monotonic() - started, 3))
            assert page['samples']['total'] == len(rows)
            assert len(page['samples']['items']) == 10
            assert page['samples']['offset'] == offset
        assert pages[0]['profile'] == pages[1]['profile'] == pages[2]['profile']
        assert not set(row['record_id'] for row in pages[0]['samples']['items']) & set(row['record_id'] for row in pages[1]['samples']['items'])
        assert pages[-1]['samples']['next_cursor'] is None
        assert pages[0]['profile']['source_counts'] == dict(Counter(row['source_system'] for row in rows))
        with factory() as db:
            profile = pages[0]['profile']
            flagged = db.query(ResolutionRecord.id).filter_by(run_id=run).filter(
                func.json_array_length(ResolutionRecord.quality_flags) > 0).count()
            assert profile['records_with_quality_flags'] == flagged
            for field in PROFILE_FIELDS:
                count = sum(row.get(field) is not None and bool(str(row[field]).strip()) for row in rows)
                assert profile['field_completeness'][field] == {'present': count, 'missing': len(rows) - count}
            summary = db.query(WorkflowArtifact).filter_by(run_id=run, phase='PROFILE').first()
            assert 'field_completeness' in summary.payload['profile']
            # Existing screenshot run exercises compatibility without changing its data/account.
            old_run = '3449137c-8f4d-41cc-9152-fa12768aa355'
            old_artifact = db.query(WorkflowArtifact.id, WorkflowArtifact.content_hash).filter_by(run_id=old_run, phase='PREPARE').first()
            if old_artifact:
                before = old_artifact.content_hash
                old_page = data_read(db, old_run, 10, None, 9990)
                assert old_page['samples']['total'] == 10000
                assert len(old_page['samples']['items']) == 10
                assert old_page['profile']['records_with_quality_flags'] is not None
                assert old_artifact.content_hash == before
                report['checks'].append('existing_10000_record_session_old_profile_compatible')
        report['profile'] = pages[0]['profile']
        report['checks'].extend(['real_upload_prepare_worker', 'first_second_last_pages_10_records', 'global_metrics_match_db', 'metrics_stable_between_pages'])
        (OUT / 'api.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        subprocess.run(['node', 'frontend/scripts/verify-data-overview.mjs'], env={**os.environ, 'OVERVIEW_EMAIL': email, 'OVERVIEW_PASSWORD': password, 'OVERVIEW_RUN': run}, check=True)
    engine.dispose()
    print('Data overview API and browser verification passed', flush=True)


if __name__ == '__main__':
    main()
