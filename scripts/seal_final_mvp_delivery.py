"""Seal non-secret source/evidence and inspect the existing local QA stack read-only."""
import hashlib
import json
import subprocess
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/final-mvp-20261015'


def command(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=True)
    return result.stdout.decode('utf-8', errors='replace').strip()


def digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    expected_compose = 'f5891727fdfcaf6d50579901036413654779b50caaab406cb05bf0fe6bc2ac81'
    assert digest(ROOT / 'docker-compose.yml') == expected_compose
    labels = json.loads(command(['docker', 'inspect', '--format', '{{json .Config.Labels}}',
                                 'p119-session-qa-database-1']))
    assert labels['com.docker.compose.project'] == 'p119-session-qa'
    assert Path(labels['com.docker.compose.project.working_dir']).resolve() == ROOT.resolve()
    endpoints = {}
    for endpoint in ('/health', '/ready'):
        with urllib.request.urlopen('http://localhost:8001' + endpoint, timeout=15) as response:
            endpoints[endpoint] = {'status_code': response.status, 'body': json.load(response)}
    with urllib.request.urlopen('http://localhost:3000/login', timeout=15) as response:
        frontend_status = response.status
    raw = command(['docker', 'compose', 'ps', '--format', 'json'])
    containers = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
    assert {item['Service'] for item in containers} == {'backend', 'worker', 'frontend', 'database'}
    assert all(item['State'] == 'running' for item in containers)
    counts_sql = "SELECT version_num FROM alembic_version; SELECT 'records|'||count(*) FROM mdm_resolution_records; SELECT 'runs|'||count(*) FROM mdm_resolution_runs; SELECT 'pairs|'||count(*) FROM mdm_workflow_pairs; SELECT 'generations|'||count(*) FROM mdm_workflow_generations;"
    counts = command(['docker', 'compose', 'exec', '-T', 'database', 'psql', '-U', 'masterone',
                      '-d', 'masterone', '-At', '-v', 'ON_ERROR_STOP=1', '-c', counts_sql]).splitlines()
    assert counts[0] == '0033_matching_foundation'
    images = {name: command(['docker', 'inspect', '--format', '{{.Image}}', f'p119-session-qa-{name}-1'])
              for name in ('backend', 'worker', 'frontend', 'database')}
    write(OUT / 'readiness-delivery.json', {'endpoints': endpoints, 'frontend_status': frontend_status,
          'containers': [{k: item.get(k) for k in ('Service', 'Name', 'State', 'Health', 'Image')} for item in containers],
          'image_ids': images, 'database': counts, 'production_deployment': False})
    source = set()
    for folder in ('backend/src', 'backend/migrations', 'backend/tests', 'frontend/src',
                   'docs/advanced-matching', 'scripts', 'benchmark/tests'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.tsx', '.ts', '.css', '.md', '.mjs', '.json', '.toml'}:
                if '__pycache__' not in path.parts and not path.name.startswith('.env'):
                    source.add(path)
    for filename in ('backend/pyproject.toml', 'backend/Dockerfile', 'frontend/Dockerfile',
                     'frontend/package.json', 'frontend/package-lock.json', '.gitattributes',
                     'MASTERONE_ADVANCED_SPEC_20261014.md'):
        path = ROOT / filename
        if path.is_file():
            source.add(path)
    archive = OUT / 'source-checkpoint.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(source):
            assert 'compose' not in path.name.lower(), path
            bundle.write(path, path.relative_to(ROOT).as_posix())
    # Diff remains local; no staging, reset, commit, or secret env files.
    (OUT / 'tracked-changes.patch').write_bytes(subprocess.run(
        ['git', 'diff', '--binary', '--', 'backend', 'frontend', 'benchmark', '.gitattributes'],
        cwd=ROOT, capture_output=True, check=True).stdout)
    write(OUT / 'source-checkpoint.json', {'head': command(['git', 'rev-parse', 'HEAD']),
          'branch': command(['git', 'branch', '--show-current']),
          'scope': 'Uncommitted working-tree snapshot; Compose/env/data/model files excluded',
          'source': {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(source)}})
    evidence = set()
    # Freeze small final-MVP evidence; large databases are governed by their own run manifests.
    for path in OUT.rglob('*'):
        if path.is_file() and path.name not in {'delivery-manifest.json', 'delivery-manifest-verification.json'}:
            if path.suffix.lower() in {'.json', '.md', '.log', '.xml', '.txt', '.png', '.csv', '.zip', '.patch'}:
                evidence.add(path)
    for folder in ('artifacts/advanced-m1-20261014/regression-si11mle0',
                   'artifacts/advanced-m2b-20261014/regression-f53mkns_',
                   'artifacts/advanced-m2b-20261014/fixed-universe-final-01',
                   'artifacts/advanced-m2b-20261014/experiments-01'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.xml', '.log', '.md'}:
                evidence.add(path)
    for name in ('requirements-lock.txt', 'model-manifest.json', 'dependency-evidence.json',
                 'inference-report.json', 'supervisor-report.json'):
        path = ROOT / 'artifacts/advanced-m2a-20261014/e5-trial-01' / name
        assert path.is_file(), path
        evidence.add(path)
    manifest = {'created_at': datetime.now(UTC).isoformat(),
                'verdict': 'CONDITIONAL_GO_BASELINE_LLM_HITL_LOCAL_QA',
                'source_checkpoint': 'artifacts/final-mvp-20261015/source-checkpoint.json',
                'compose_sha256': expected_compose, 'production_deployment': False,
                'latest_live_tc': 'docker-live-tc-04/report.json', 'latest_browser': 'browser-05/report.json',
                'flags': {'advanced_matching_agents_enabled': False, 'advanced_matching_integration_enabled': False,
                          'pair_automation_enabled': False, 'entity_structure_enabled': True},
                'limitations': ['No independent DEV/gold; no trained thresholds',
                                'Full E3 candidate cap failure; Linux embedding disabled',
                                'Application R2 disabled; isolated connectivity probe only'],
                'files': {p.relative_to(ROOT).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(p)}
                          for p in sorted(evidence)}}
    write(OUT / 'delivery-manifest.json', manifest)
    for filename, metadata in manifest['files'].items():
        assert digest(ROOT / filename) == metadata['sha256'], filename
    write(OUT / 'delivery-manifest-verification.json', {'status': 'PASS', 'files_verified': len(evidence),
           'manifest_sha256': digest(OUT / 'delivery-manifest.json'), 'compose_unchanged': True})
    print(json.dumps({'status': 'PASS', 'source_files': len(source), 'evidence_files': len(evidence),
                      'database': counts, 'production_deployment': False}))


if __name__ == '__main__':
    main()
