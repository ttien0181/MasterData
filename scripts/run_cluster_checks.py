"""Run cluster checks against owned local DB/store state, preserving full logs."""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from run_merge_baseline import ReportStateDirectory, isolated_environment

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    'golden': ['test_survivorship_policy.py', 'test_safe_clustering.py', 'test_golden_and_benchmark.py',
               'test_cluster_guards.py', 'test_workflow_golden.py', 'test_workflow_audit_fixes.py'],
    'hash': ['test_workflow_digest.py', 'test_workflow_audit_fixes.py', 'test_workflow_plan_cache.py',
             'test_workflow_handoff_storage.py', 'test_workflow_finalize.py'],
    'components': ['test_reviewed_components.py', 'test_safe_clustering.py', 'test_workflow_group_approvals.py',
                   'test_workflow_partial_publication.py', 'test_workflow_plan_cache.py', 'test_recovered_publication.py'],
    'groups': ['test_large_group_review.py', 'test_workflow_group_approvals.py', 'test_workflow_cluster_preview.py'],
    'identity': ['test_workflow_entity_identity.py', 'test_workflow_golden.py', 'test_workflow_audit_fixes.py'],
    'singletons': ['test_virtual_singletons.py', 'test_workflow_migrations.py', 'test_workflow_golden.py'],
    'storage': ['test_workflow_snapshot_streaming.py', 'test_workflow_handoff_storage.py'],
    'workflow': ['test_workflow_streamed_publication.py', 'test_workflow_golden.py', 'test_workflow_audit_fixes.py',
                 'test_workflow_publication_fences.py', 'test_workflow_retry.py', 'test_workflow_recovery.py'],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=[*SUITES, 'all'], required=True)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('pytest_args', nargs='*')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('Timeout must be positive')
    reports = ROOT / 'artifacts/merge-baseline'
    reports.mkdir(parents=True, exist_ok=True)
    report = Path(tempfile.mkdtemp(prefix='cluster-' + args.suite + '-', dir=reports))
    paths = ['backend/tests'] if args.suite == 'all' else ['backend/tests/test_mdm/' + n for n in SUITES[args.suite]]
    with ReportStateDirectory(prefix='state-', dir=report) as state:
        env = isolated_environment(Path(state))
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['WORKFLOW_ARTIFACT_DIR'] = str(Path(state) / 'workflow-artifacts')
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'pytest', *paths,
                   '-p', 'pytest_asyncio.plugin', '-p', 'tests.merge_baseline_plugin',
                   '-p', 'no:cacheprovider', '-q', '--tb=short',
                   '--basetemp=' + str(Path(state) / 'pytest'), *args.pytest_args]
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=args.timeout)
            output, code = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as error:
            output = (error.stdout or b'').decode('utf-8', 'replace') + '\nCLUSTER_CHECK_TIMEOUT\n'
            code = 124
        (report / 'pytest.log').write_text(output, encoding='utf-8')
        print('\n'.join(output.splitlines()[-65:]))
        print('REPORT:', report, flush=True)
        return code


if __name__ == '__main__':
    sys.exit(main())
