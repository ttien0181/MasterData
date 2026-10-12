"""Capture reviewable BE source evidence without environment files or credentials."""
import json
import subprocess
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/session-workflow-2sessions/20261012'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def main():
    initial = json.loads((OUT / 'source.json').read_text(encoding='utf-8'))
    tracked = git('diff', '--name-only', '--', 'backend', 'docker-compose.yml').splitlines()
    additions = git('ls-files', '--others', '--exclude-standard', '--', 'backend').splitlines()
    scripts = ['scripts/' + name for name in ('capture_workflow_baseline.py', 'session_workflow_log.py',
        'verify_session_workflow_postgres.py', 'verify_session_workflow_qa.py',
        'reproduce_workflow_baseline.py', 'capture_workflow_handoff.py', 'verify_workflow_handoff.py')]
    files = sorted(set(tracked + additions + scripts))
    hashes = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in files if (ROOT / name).is_file()}
    protected = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in initial['protected_sha256']}
    changed = [name for name, value in protected.items() if initial['protected_sha256'][name] != value]
    assert changed == ['backend/src/masterone/mdm/pipeline/stage_runner.py'], changed
    assert not git('diff', '--name-only', '--', 'frontend')
    assert not (ROOT / 'docker-compose.qa.yml').exists()
    report = {'head': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
        'files': hashes, 'protected_sha256': protected, 'protected_changes': changed,
        'frontend_diff': [], 'new_compose_files': [], 'existing_deleted_qa_compose_preserved': True,
        'baseline_sha256': sha256((OUT / 'baseline.json').read_bytes()).hexdigest(),
        'source_state': 'uncommitted_changes_preserved_for_FE_session'}
    (OUT / 'source-final.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # Capture tracked patch plus additions; exclude all .env files and unrelated docs/tmp folders.
    patch = subprocess.check_output(['git', 'diff', '--', *tracked], cwd=ROOT)
    for name in files:
        if name in tracked:
            continue
        result = subprocess.run(['git', 'diff', '--no-index', '--', '/dev/null', name], cwd=ROOT,
                                capture_output=True, check=False)
        if result.returncode not in (0, 1):
            raise RuntimeError('DIFF_CAPTURE_FAILED')
        patch += result.stdout
    (OUT / 'backend-source.patch').write_bytes(patch)
    python_files = [name for name in files if name.startswith('backend/') and name.endswith('.py')]
    (OUT / 'ruff-files.json').write_text(json.dumps(python_files, indent=2), encoding='utf-8')
    check = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'ruff', 'check', *python_files],
                           cwd=ROOT, capture_output=True, check=False)
    (OUT / 'ruff.log').write_bytes(check.stdout + check.stderr)
    print(json.dumps({'files': len(files), 'protected_changes': changed, 'ruff_exit': check.returncode}))
    return check.returncode


if __name__ == '__main__':
    raise SystemExit(main())
