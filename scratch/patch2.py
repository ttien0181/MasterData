import re

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(
    'ext_id = r.get("source_record_id") or r.get("external_id")',
    'ext_id = r.get("source_record_id") or r.get("external_id") or r.get("id")'
)

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'w', encoding='utf-8') as f:
    f.write(code)
