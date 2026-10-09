import re

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(
    '        # Write SUCCESS for HOP-11\n        success_path = _artifact_path(ctx_prep, "_SUCCESS.json")\n        with open(success_path, "w") as f:',
    '        # Write SUCCESS for HOP-11\n        success_path = _artifact_path(ctx_prep, "_SUCCESS.json")\n        with contextlib.suppress(Exception):\n            os.chmod(success_path, stat.S_IWRITE)\n        with open(success_path, "w") as f:'
)

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'w', encoding='utf-8') as f:
    f.write(code)
