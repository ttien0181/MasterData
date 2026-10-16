"""Reproduce M1 checks in owned scratch and explicitly verified PostgreSQL QA."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from run_merge_baseline import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('foundation', 'regression'), default='foundation')
    args = parser.parse_args()
    base = ROOT / 'artifacts/advanced-m1-20261014'
    target = json.loads((base / 'qa-postgresql-target.json').read_text(encoding='utf8'))
    identifier = subprocess.check_output(['docker', 'inspect', '--format', '{{.Id}}', target['container']], text=True).strip()
    if identifier != target['container_id'] or target['compose_project'] != 'p119-session-qa':
        raise RuntimeError('QA_CONTAINER_IDENTITY_CHANGED')
    if not target['new_database'].startswith('masterone_m1_qa_') or not target['new_owner_role'].startswith('m1_qa_'):
        raise RuntimeError('QA_DATABASE_IDENTITY_INVALID')
    out = Path(tempfile.mkdtemp(prefix=args.suite + '-', dir=base))
    scratch_root = ROOT / 'benchmark/.cache/m1-checks'
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='owned-', dir=scratch_root))
    env = isolated_environment(scratch)
    env.update({'MASTERONE_ENV_FILE': str(out / 'absent.env'), 'PYTHONDONTWRITEBYTECODE': '1',
        'MATCHING_PROFILE_REGISTRY_ENABLED': 'false', 'STEWARD_FEEDBACK_CAPTURE_ENABLED': 'false',
        'ADVANCED_EVALUATION_REGISTRY_ENABLED': 'false', 'M1_QA_VERIFIED_CONTAINER_ID': identifier,
        'M1_QA_POSTGRESQL_URL': f"postgresql+psycopg://{target['new_owner_role']}:m1-isolated-qa-only@127.0.0.1:{target['port']}/{target['new_database']}"})
    env['PYTHONPATH'] += os.pathsep + str(ROOT / 'benchmark')
    paths = ['backend/tests/test_mdm/test_matching_foundation.py',
             'backend/tests/test_mdm/test_matching_foundation_postgresql.py',
             'backend/tests/test_mdm/test_matching_foundation_upgrade.py']
    if args.suite == 'regression':
        prior = json.loads((ROOT / 'artifacts/advanced-m0-20261014/regression-f9xv_ihd/command.json').read_text(encoding='utf8'))
        paths += [path for path in prior if path.replace('\\', '/').startswith('backend/tests/')]
        paths += ['backend/tests/test_mdm/' + name for name in (
            'test_auth.py', 'test_rbac.py', 'test_migrations.py', 'test_workflow_migrations.py',
            'test_workflow_group_approvals.py', 'test_workflow_automation.py', 'test_automation_migration.py')]
        paths += ['benchmark/tests']
    cmd = [sys.executable, '-B', '-X', 'utf8', '-m', 'pytest', *paths,
           '-p', 'pytest_asyncio.plugin', '-p', 'tests.merge_baseline_plugin', '-p', 'no:cacheprovider',
           '-q', '--tb=short', '--basetemp=' + str(scratch / 'pytest'), '--junitxml=' + str(out / 'junit.xml')]
    (out / 'command.json').write_text(json.dumps(cmd, ensure_ascii=False, indent=2), encoding='utf8')
    print('REPORT', out, flush=True)
    with (out / 'pytest.log').open('w', encoding='utf8') as log:
        result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    print((out / 'pytest.log').read_text(encoding='utf8')[-9000:], flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
