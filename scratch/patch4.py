import re

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(
    '        if prepared_records:',
    '        import contextlib\n        with contextlib.suppress(Exception): os.remove(prep_path)\n        if prepared_records:'
)

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'w', encoding='utf-8') as f:
    f.write(code)
