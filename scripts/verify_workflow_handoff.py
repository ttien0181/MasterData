"""Read-only final evidence check; never emits environment credentials."""
import json
from hashlib import sha256
from pathlib import Path

import httpx
from masterone.config import Settings
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionRun

OUT = Path('artifacts/session-workflow-2sessions/20261012')


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))


def main():
    api, worker, source, smoke = [read_json(OUT / name) for name in
        ('runtime-api.json', 'runtime-worker.json', 'source-final.json', 'qa-smoke.json')]
    assert api == worker
    assert smoke['status'] == 'PASS'
    for module, value in api['source'].items():
        path = 'backend/src/' + module.replace('.', '/') + '.py'
        assert source['files'][path] == value
    qa = Settings(_env_file='.env.qa')
    assert api['database_fingerprint'] == sha256(qa.active_mdm_database_url.encode()).hexdigest()
    assert api['profile'] == 'neon' and api['workflow_enabled']
    assert api['llm_enabled'] == qa.llm_enabled and api['semantic_enabled'] == qa.semantic_address_enabled
    engine, factory = create_database(qa.active_mdm_database_url)
    try:
        with factory() as db:
            run = db.get(ResolutionRun, smoke['runs'][0])
            assert run.owner_user_id == smoke['owner_id'] and run.workflow_state == 'FINALIZED'
            state = {key: getattr(run, key) for key in ('workflow_state', 'workflow_revision',
                     'decision_revision', 'cluster_revision', 'golden_revision')}
    finally:
        engine.dispose()
    response = httpx.get('http://localhost:8001/openapi.json', timeout=30)
    response.raise_for_status()
    schema = response.json()
    schema['paths'] = {key: value for key, value in schema['paths'].items() if '/workflow' in key}
    (OUT / 'workflow-openapi.json').write_text(json.dumps(schema, indent=2), encoding='utf-8')
    report = {'status': 'PASS', 'api_worker_same_source': True, 'api_worker_same_qa_database': True,
              'runtime_matches_local_source': True, 'final_state': state, 'workflow_paths': len(schema['paths'])}
    (OUT / 'handoff-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
