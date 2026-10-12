"""Capture baseline in a separate process before changing engine orchestration."""
import json
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from masterone.config import Settings, get_settings
from masterone.mdm.engine_adapter import FEATURES
from tests.test_mdm.test_workflow_engine_preservation import canonical_baseline

BASE = Path('artifacts/session-workflow-2sessions/20261012/baseline.json')

settings = get_settings()
settings.semantic_address_enabled = False
settings.llm_enabled = False
BASE.parent.mkdir(parents=True, exist_ok=True)
with TemporaryDirectory() as scratch:
    BASE.write_text(json.dumps(canonical_baseline(Path(scratch)), sort_keys=True, ensure_ascii=False, indent=2), encoding='utf-8')
BASE.with_suffix('.sha256').write_text(sha256(BASE.read_bytes()).hexdigest(), encoding='utf-8')
qa = Settings(_env_file='.env.qa')
protected = sorted(Path('backend/src/masterone/mdm/pipeline').glob('*.py'))
metadata = {
    'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'iamntn': subprocess.check_output(['git', 'rev-parse', 'IamNTN'], text=True).strip(),
    'initial_status': subprocess.check_output(['git', 'status', '--short'], text=True),
    'qa_engine_flags': {f: getattr(qa, f) for f in FEATURES},
    'qa_profile': qa.mdm_database_profile,
    'qa_llm_enabled': qa.llm_enabled,
    'protected_sha256': {p.as_posix(): sha256(p.read_bytes()).hexdigest() for p in protected},
}
BASE.with_name('source.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
# Test failures must not retain credentials in logs.
Path('.workflow-red-config.log').write_text('RED: ENV_SELECTION_MISMATCH; 1 failed. Database values redacted.\n', encoding='utf-8')
print('Baseline saved; engine flags and source hashes recorded without secrets.')
