"""Run selected regressions with original orchestration modules, without checkout."""
import os
import subprocess
import sys
import types

os.environ['MASTERONE_ENV_FILE'] = ''
os.environ['LLM_ENABLED'] = 'false'
os.environ['SEMANTIC_ADDRESS_ENABLED'] = 'false'
for key in ('R2_ENDPOINT_URL', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET'):
    os.environ[key] = ''

for name in ('masterone.mdm.resolution_service', 'masterone.mdm.pipeline.stage_runner'):
    path = 'backend/src/' + name.replace('.', '/') + '.py'
    source = subprocess.check_output(['git', 'show', '2caf9459f97cdc62fe139dc2df01399b5c035475:' + path]).decode('utf-8')
    module = types.ModuleType(name)
    from pathlib import Path
    module.__file__ = str(Path(path).resolve())
    sys.modules[name] = module
    exec(compile(source, module.__file__, 'exec'), module.__dict__)

import pytest

raise SystemExit(pytest.main(sys.argv[1:]))
