"""Owned QA tests, no ambient .env or production DB."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from run_merge_baseline import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--filter', default='')
    args = parser.parse_args()
    base = ROOT / 'artifacts/final-mvp-20261015'
    base.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='checks-', dir=base))
    env = isolated_environment(out)
    env.update({'MASTERONE_ENV_FILE': str(out / 'absent.env'), 'MATCHING_PROFILE_REGISTRY_ENABLED': 'false',
                'STEWARD_FEEDBACK_CAPTURE_ENABLED': 'false', 'ADVANCED_EVALUATION_REGISTRY_ENABLED': 'false',
                'ADVANCED_MATCHING_AGENTS_ENABLED': 'false', 'ADVANCED_MATCHING_INTEGRATION_ENABLED': 'false'})
    paths = [str(ROOT / 'backend/tests/test_mdm' / name) for name in
             ('test_matching_learning.py', 'test_workflow_structure.py')]
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'pytest', *paths, '-q', '--tb=short',
               '-p', 'pytest_asyncio.plugin', '-p', 'tests.merge_baseline_plugin', '-p', 'no:cacheprovider',
               '--basetemp=' + str(out / 'pytest'), '--junitxml=' + str(out / 'junit.xml')]
    if args.filter:
        command += ['-k', args.filter]
    (out / 'command.json').write_text(json.dumps(command, indent=2), encoding='utf-8')
    result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8')
    (out / 'pytest.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print(out)
    print((result.stdout + result.stderr)[-5000:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
