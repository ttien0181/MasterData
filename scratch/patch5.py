import re

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(
    'with contextlib.suppress(Exception): os.remove(prep_path)',
    'import stat\n        with contextlib.suppress(Exception):\n            os.chmod(prep_path, stat.S_IWRITE)\n            os.remove(prep_path)'
)

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'w', encoding='utf-8') as f:
    f.write(code)
