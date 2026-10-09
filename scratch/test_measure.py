import sys
import traceback
try:
    with open('scratch/measure_pipeline.py', encoding='utf-8') as f:
        exec(f.read())
except BaseException as e:
    print("Caught exception:", type(e), e)
    traceback.print_exc()
