"""Capture reviewable FE patch including untracked files, while protecting Session 1."""
import json
import subprocess
from hashlib import sha256
from pathlib import Path

OUT = Path('artifacts/session-workflow-2sessions/20261012-fe')


def git(*args):
    return subprocess.check_output(['git', *args]).decode('utf-8')


def main():
    baseline = json.loads(Path('artifacts/session-workflow-2sessions/20261012/source-final.json').read_text(encoding='utf-8'))
    protected = {name: digest for name, digest in baseline['files'].items() if name.startswith('backend/')}
    for name, digest in protected.items():
        assert sha256(Path(name).read_bytes()).hexdigest() == digest, 'SESSION1_SOURCE_CHANGED_' + name
    names = set(git('diff', '--name-only', 'HEAD', '--', 'frontend').splitlines())
    new = git('ls-files', '--others', '--exclude-standard', '--', 'frontend').splitlines()
    names.update(new)
    patch = git('diff', 'HEAD', '--', 'frontend')
    for name in new:
        lines = Path(name).read_text(encoding='utf-8').splitlines()
        patch += f'diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n'
        patch += ''.join('+' + line + '\n' for line in lines)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'frontend-source.patch').write_text(patch, encoding='utf-8')
    report = {'head': git('rev-parse', 'HEAD').strip(), 'branch': git('branch', '--show-current').strip(),
        'session1_source_unchanged': True, 'protected_backend_files': len(protected),
        'files': {name: sha256(Path(name).read_bytes()).hexdigest() for name in sorted(names)},
        'patch_sha256': sha256(patch.encode()).hexdigest()}
    (OUT / 'frontend-source.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'frontend_files': len(names), 'protected_backend_files': len(protected)}))


if __name__ == '__main__':
    main()
