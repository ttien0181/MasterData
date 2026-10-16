"""M2-B tests in owned QA state, never developer database settings."""
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
    parser.add_argument('--suite', choices=('unit', 'integration', 'regression'), default='unit')
    parser.add_argument('--filter', default='')
    args = parser.parse_args()
    base = ROOT / 'artifacts/advanced-m2b-20261014'
    base.mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=args.suite + '-', dir=base))
    env = isolated_environment(out)
    env.update({'MASTERONE_ENV_FILE': str(out / 'absent.env'),
                'ADVANCED_MATCHING_INTEGRATION_ENABLED': 'false',
                'ADVANCED_MATCHING_AGENTS_ENABLED': 'false',
                'MATCHING_PROFILE_REGISTRY_ENABLED': 'false',
                'STEWARD_FEEDBACK_CAPTURE_ENABLED': 'false',
                'ADVANCED_EVALUATION_REGISTRY_ENABLED': 'false', 'MDM_SCORE_WORKERS': '1'})
    names = ('test_matching_candidates.py', 'test_matching_coordinator.py', 'test_matching_integration.py')
    if args.suite != 'unit':
        names += ('test_matching_rebuild.py',)
    paths = [str(ROOT / 'backend/tests/test_mdm' / name) for name in names
             if (ROOT / 'backend/tests/test_mdm' / name).exists()]
    if args.suite == 'regression':
        paths += [str(ROOT / 'backend/tests/test_mdm' / name) for name in
                  ('test_candidate_engine.py', 'test_sparse_tfidf.py', 'test_scoring_engine.py',
                   'test_workflow_review_routing.py', 'test_matching_agents_contracts.py',
                   'test_matching_agents_workflow.py')]
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'pytest', *paths,
               '-p', 'pytest_asyncio.plugin', '-p', 'tests.merge_baseline_plugin',
               '-p', 'no:cacheprovider', '-q', '--tb=short',
               '--basetemp=' + str(out / 'pytest'), '--junitxml=' + str(out / 'junit.xml')]
    if args.filter:
        command += ['-k', args.filter]
    (out / 'command.json').write_text(json.dumps(command, indent=2), encoding='utf-8')
    result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8')
    (out / 'pytest.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print(out)
    print((result.stdout + result.stderr)[-6000:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
