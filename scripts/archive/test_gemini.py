import asyncio
import os
import sys

# Add backend/src to path
sys.path.insert(0, r"E:\AI_VIN\DemoDay\P-119\backend\src")

from masterone.config import get_settings
from masterone.mdm.gemini_explanation import explain_cohort

payload = {
    "record_count": 2,
    "pair_count": 1,
    "field_state_counts": {"name": {"MATCH": 1}},
    "reason_counts": {"EXACT_MATCH": 1},
    "score_is_probability": False
}

os.environ["LLM_ENABLED"] = "false"  # just in case
settings = get_settings()

try:
    res = explain_cohort(payload, settings)
    print("SUCCESS", res)
except Exception as e:
    print("FAILED", e, getattr(e, 'detail', ''))
