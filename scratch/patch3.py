import re

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace(
    'if "entity_type" not in nr:\n                nr["entity_type"] = "PERSON"',
    'if "entity_type" not in nr:\n                nr["entity_type"] = "PERSON"\n            if "external_id" not in nr:\n                nr["external_id"] = nr.get("id", "")'
)

with open('backend/src/masterone/mdm/pipeline/stage_runner.py', 'w', encoding='utf-8') as f:
    f.write(code)
