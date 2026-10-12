"""Recheck the existing audit runs; no additional benchmark TC or source data."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from masterone.auth.models import User
from masterone.auth.security import password_hash
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import LLMReservation, ResolutionRun


def main():
    out = Path('artifacts/audit-20261012')
    report = json.loads((out / 'runtime.json').read_text(encoding='utf-8'))
    run, smoke = report['run_id'], report['smoke_run_id']
    engine, factory = create_database(get_settings().active_mdm_database_url)
    password = secrets.token_urlsafe(32)
    with factory.begin() as db:
        user = db.get(User, db.get(ResolutionRun, run).owner_user_id)
        assert user.email.startswith('audit-') and user.role == 'STEWARD'
        email = user.email
        user.password_hash = password_hash.hash(password)  # Only this script's synthetic account.
    if '--browser-only' in sys.argv:
        env = {**os.environ, 'AUDIT_EMAIL':email, 'AUDIT_PASSWORD':password, 'AUDIT_RUN':run, 'AUDIT_SMOKE_RUN':smoke}
        subprocess.run(['node','frontend/scripts/audit-workflow-browser.mjs'], env=env, check=True)
        report.pop('failure',None)
        report['checks'].append('final_packaged_browser_desktop_mobile')
        (out / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        engine.dispose()
        return
    with httpx.Client(base_url='http://localhost:8001/api/v1', headers={'Origin':'http://localhost:3000'}, timeout=180) as client:
        login = client.post('/auth/login', json={'email':email,'password':password})
        assert login.status_code == 200
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        timings = []
        for _ in range(5):
            started = time.monotonic()
            state = client.get(f'/mdm/runs/{run}/workflow')
            assert state.status_code == 200
            timings.append(round(time.monotonic()-started,3))
        report['status_http_seconds_after_query_batching'] = timings
        print('Revised status timings:', timings, flush=True)
        page = client.get(f'/mdm/runs/{run}/workflow/pairs?group=C&limit=25').json()
        if not page['items']:
            page = client.get(f'/mdm/runs/{run}/workflow/pairs?group=A&limit=25').json()
        pair = next(p for p in page['items'] if p['baseline'].get('field_evidence'))
        body = {'request_id':str(uuid4()), 'expected_workflow_revision':state.json()['workflow_revision']}
        url = f'/mdm/runs/{run}/workflow/pairs/{pair["pair_id"]}/explanation'
        explanation = client.post(url, json=body)
        assert explanation.status_code == 200
        result = explanation.json()
        report['live_explanation_with_evidence'] = result
        if result['status'] == 'READY':
            assert client.post(url, json={**body,'request_id':str(uuid4())}).json()['request_id'] == result['request_id']
            with factory() as db:
                row = db.get(LLMReservation, result['request_id'])
                assert row.state == 'SUCCESS'
                report['llm_durable_evidence'] = {'state':row.state,'calls':row.calls,
                                                 'response_hash_present':row.response is not None}
        print('Provider evidence:', result['status'], result.get('provider'), result.get('error_code'), flush=True)
        (out / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        env = {**os.environ, 'AUDIT_EMAIL':email, 'AUDIT_PASSWORD':password, 'AUDIT_RUN':run, 'AUDIT_SMOKE_RUN':smoke}
        subprocess.run(['node','frontend/scripts/audit-workflow-browser.mjs'], env=env, check=True)
        report.pop('failure',None)
        report['checks'].append('revised_packaged_browser_desktop_mobile')
        (out / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    engine.dispose()


if __name__ == '__main__':
    main()
