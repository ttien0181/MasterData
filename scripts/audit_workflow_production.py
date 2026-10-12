"""One randomly selected <=10k TC; HTTP/worker verification with redacted evidence."""
import csv
import io
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

os.environ['MASTERONE_ENV_FILE'] = '.env.qa'

import httpx
import pyarrow.parquet as pq
from masterone.auth.models import User
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import LLMPolicyControl

OUT = Path('artifacts/audit-20261012')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    engine, factory = create_database(settings.active_mdm_database_url)
    email, password = 'audit-' + uuid4().hex + '@example.com', secrets.token_urlsafe(32)
    origin = 'http://localhost:3000'
    client = httpx.Client(base_url='http://localhost:8001/api/v1', headers={'Origin': origin}, timeout=180)
    report = {'checks': [], 'dataset': json.loads(Path('artifacts/audit-20261012-dataset.json').read_text(encoding='utf-8'))}
    def save():
        (OUT / 'runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    def call(method, path, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code != expected:
            detail = response.json().get('detail') if 'json' in response.headers.get('content-type','') else None
            code = detail.get('code') if isinstance(detail,dict) else 'HTTP_ERROR'
            raise RuntimeError(f'{method} {path.split("/")[-1]} HTTP {response.status_code} {code}')
        return response.json()
    def state(run):
        return call('GET', f'/mdm/runs/{run}/workflow')
    def body(run, golden=False, **extra):
        s = state(run)
        b = {'request_id': str(uuid4()), 'expected_workflow_revision': s['workflow_revision']}
        if golden:
            b.update(expected_cluster_revision=s['cluster_revision'], expected_golden_revision=s['golden_revision'])
        return {**b,**extra}
    def wait_operation(run, op):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            found = call('GET', f'/mdm/runs/{run}/workflow/operations/{op}')
            if found['status'] == 'COMPLETED':
                return found
            if found['status'] in ('FAILED','RETRYABLE_FAILED','CANCELLED'):
                raise RuntimeError('Worker ' + str(found['error_code']))
            time.sleep(1)
        raise TimeoutError('Worker operation timeout')
    try:
        call('POST','/auth/register',201,json={'email':email,'password':password})
        with factory.begin() as db:
            db.query(User).filter_by(email_normalized=email).one().role = 'STEWARD'
        login = call('POST','/auth/login',json={'email':email,'password':password})
        client.headers['X-CSRF-Token'] = login['csrf_token']
        dataset = report['dataset']
        source = next(Path(dataset['path']).rglob('prepare/records.parquet'))
        parquet = pq.ParquetFile(source)
        raw_cols = [c for c in parquet.schema_arrow.names if c.startswith('raw__')]
        cols = [c[5:] for c in raw_cols]
        assert not any(c in cols for c in ('entity_id','truth','cluster_id'))
        rows = []
        for batch in parquet.iter_batches(batch_size=1000, columns=raw_cols):
            rows.extend([{c[5:]: r[c] for c in raw_cols} for r in batch.to_pylist()])
            if len(rows) >= 10000:
                rows = rows[:10000]
                break
        files = []
        for i in range(0,len(rows),5000):
            buf = io.StringIO(newline='')
            writer = csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows[i:i+5000])
            files.append(('files',(f'{dataset["tc"]}-{i//5000+1}.csv',buf.getvalue().encode('utf-8'),'text/csv')))
        report['dataset'].update(artifact_rows=parquet.metadata.num_rows, used_rows=len(rows), input_projection='raw__ fields only; first 10000; no truth', files=len(files))
        started = time.monotonic()
        created = call('POST','/mdm/runs',201,data={'workflow_version':'2'},files=files)
        run = created['run_id']
        report['run_id'] = run
        save()
        print('TC upload created:',run,flush=True)
        while state(run)['workflow_state'] == 'DATA_PREPARING':
            current = state(run)
            if current['retryable_operation']:
                raise RuntimeError('PREPARE worker failure ' + str(current['retryable_operation']['error_code']))
            if time.monotonic() - started > 600:
                raise TimeoutError('PREPARE timeout')
            time.sleep(1)
        report['prepare_seconds'] = round(time.monotonic()-started,3)
        assert state(run)['counts']['records']['value'] == len(rows)
        report['checks'].append('multi_file_upload_prepare_10000')
        op = call('POST',f'/mdm/runs/{run}/workflow/start-decide',202,json=body(run))
        started = time.monotonic()
        wait_operation(run,op['operation_id'])
        report['decide_seconds'] = round(time.monotonic()-started,3)
        report['state'] = state(run)
        timings = []
        for _ in range(5):
            started = time.monotonic()
            state(run)
            timings.append(round(time.monotonic()-started,3))
        report['status_http_seconds'] = timings
        page = call('GET',f'/mdm/runs/{run}/workflow/pairs?limit=25')
        report['candidate_count'] = page['total']
        report['checks'].append('tc_decide_worker_and_status_reads')
        save()
        print('TC classification complete:',page['total'],'pairs; status timings',timings,flush=True)

        # Small separate synthetic fixture exercises mutation, confirmation and export.
        data = b'source_record_id,source_system,name,phone,email,address\n1,CRM,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n2,BILL,Nguyen Van An,0901234567,a@example.test,123 Hanoi\n3,CRM,Tran Thi Binh,0987654321,b@example.test,456 HCM\n'
        small = call('POST','/mdm/runs',201,data={'workflow_version':'2'},files={'files':('audit-smoke.csv',data,'text/csv')})['run_id']
        report['smoke_run_id'] = small
        while state(small)['workflow_state'] == 'DATA_PREPARING':
            time.sleep(1)
        op = call('POST',f'/mdm/runs/{small}/workflow/start-decide',202,json=body(small))
        wait_operation(small,op['operation_id'])
        url = f'/mdm/runs/{small}/workflow'
        pairs = call('GET',url+'/pairs')['items']
        for pair in pairs:
            if pair['baseline']['decision'] not in ('MERGEABLE','BLOCKED','NOT_MERGEABLE','NON_MATCH'):
                call('POST',url+f'/pairs/{pair["pair_id"]}/decision',json=body(small,expected_pair_revision=pair['revision'],decision='no_merge'))
        op = call('POST',url+'/finalize-decisions',202,json=body(small,expected_decision_revision=state(small)['decision_revision']))
        wait_operation(small,op['operation_id'])
        op = call('POST',url+'/golden/resolve',202,json=body(small,True,expected_decision_revision=state(small)['decision_revision']))
        resolved = wait_operation(small,op['operation_id'])
        report['resolved'] = resolved['result']
        assert resolved['result']['confirmed'] > 0
        records = call('GET',url+'/golden')['items']
        assert all(g['status']=='CONFIRMED' for g in records)
        call('POST',url+'/finalize-golden',json=body(small,expected_golden_revision=state(small)['golden_revision']))
        preview = call('POST',f'/mdm/runs/{small}/previews',json={'choices':{},'acknowledge_unreviewed':False})
        report['export_preview'] = {'rows':preview['estimated_output_rows'],'result_hash':preview['result_hash']}
        report['checks'].append('synthetic_finalize_resolve_official_golden_export')

        with factory.begin() as db:
            control = db.get(LLMPolicyControl,'global')
            if control is None:
                control = LLMPolicyControl(id='global',enabled=True,revision=1)
                db.add(control)
            elif not control.enabled:
                control.enabled = True
                control.revision += 1
        pair = page['items'][0]
        explain_url = f'/mdm/runs/{run}/workflow/pairs/{pair["pair_id"]}/explanation'
        explanation = call('POST',explain_url,json=body(run))
        report['live_explanation'] = explanation
        if explanation['status']=='READY':
            cached = call('POST',explain_url,json=body(run))
            assert cached['request_id'] == explanation['request_id']
            report['checks'].append('live_provider_explanation_and_cache')
        else:
            report['checks'].append('live_provider_fallback_visible')
        save()
        env = {**os.environ,'AUDIT_EMAIL':email,'AUDIT_PASSWORD':password,'AUDIT_RUN':run,'AUDIT_SMOKE_RUN':small}
        subprocess.run(['node','frontend/scripts/audit-workflow-browser.mjs'],env=env,check=True)
        report['checks'].append('packaged_browser_desktop_mobile')
        save()
        print('Runtime audit complete:',report['checks'],flush=True)
    except BaseException as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)
        save()
        raise
    finally:
        client.close()
        engine.dispose()


if __name__ == '__main__':
    main()
