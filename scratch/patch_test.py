import os

with open('backend/tests/test_mdm/test_stage_runner.py', 'r', encoding='utf-8') as f:
    t = f.read()
if 'os.environ["SOURCE_MAPPING_VERSION"]' not in t:
    t = "import os\nos.environ['SOURCE_MAPPING_VERSION'] = '1.0'\n" + t
    with open('backend/tests/test_mdm/test_stage_runner.py', 'w', encoding='utf-8') as f:
        f.write(t)
