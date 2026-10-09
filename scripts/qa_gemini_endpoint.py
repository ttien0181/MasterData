"""One additional synthetic Gemini endpoint canary, plus a cache replay. No retry."""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from qa_live_system import DB, ROOT
from run_merge_baseline import isolated_environment

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--post-fix", action="store_true", help="Final fourth request after enum-schema fix; never an automatic retry")
args = parser.parse_args()
prior_requests = 2
if args.post_fix:
    previous = json.loads((ROOT / "artifacts/full-system-20261009/gemini-endpoint/results.json").read_text(encoding="utf-8"))
    assert previous["status"] == "FAIL" and previous["total_generation_requests_started"] == 3
    prior_requests = 3
output = Path("artifacts/full-system-20261009/gemini-endpoint-final" if args.post_fix else "artifacts/full-system-20261009/gemini-endpoint")
output.mkdir(parents=True, exist_ok=False)  # Refuse accidental paid reruns.
prior = json.loads((ROOT / "artifacts/full-system-20261009/gemini-canary.json").read_text(encoding="utf-8"))
assert len(prior["cases"]) == 2 and prior["authorized_budget_usd"] == 0.1
os.environ.update(isolated_environment(output / "state"))
cloud = dotenv_values(ROOT / ".env.qa")
local = dotenv_values(ROOT / ".env.qa.local")
key = cloud.get("GEMINI_API_KEY") or next((cloud.get(f"GEMINI_API_KEY_{i}") for i in (1, 2, 3) if cloud.get(f"GEMINI_API_KEY_{i}")), None)
assert key
os.environ.update(APP_ENV="test", MDM_DATABASE_URL=DB, MDM_DATABASE_URL_LOCAL=DB,
                  AUTH_CSRF_SECRET=local["AUTH_CSRF_SECRET"], CORS_ORIGINS="http://127.0.0.1:13000",
                  GEMINI_API_KEY=key, GEMINI_MODEL="gemini-3.5-flash-lite", GEMINI_MAX_RETRIES="0",
                  GEMINI_MAX_OUTPUT_TOKENS="512", LLM_ENABLED="true", LLM_EXPLANATION_ENABLED="true")
sys.path.insert(0, str(ROOT / "backend/src"))
from fastapi.testclient import TestClient
from masterone.api import mdm_resolution
from masterone.main import app

calls = [0]
original = mdm_resolution.explain_cohort
result = {"scope": "actual explanation API + local QA PG, real Gemini, synthetic aggregates only",
          "authorized_total_budget_usd": 0.1, "prior_generation_requests": prior_requests, "retry_count": 0,
          "new_generation_requests_max": 1, "status": "STARTED"}


def save():
    (output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def bounded(payload, settings):
    assert calls[0] == 0 and payload["record_count"] <= 7 and payload["pair_count"] <= 21
    assert set(payload) == {"record_count", "pair_count", "field_state_counts", "reason_counts", "score_is_probability"}
    assert len(json.dumps(payload)) < 2048
    calls[0] += 1
    result["new_generation_requests_started"] = calls[0]
    save()  # Reserve the call before touching the provider.
    return original(payload, settings)


mdm_resolution.explain_cohort = bounded
try:
    run_id = json.loads((ROOT / "artifacts/full-system-20261009/browser-real-merge/results.json").read_text(encoding="utf-8"))["run_id"]
    with TestClient(app, headers={"Origin": "http://127.0.0.1:13000"}) as client:
        login = client.post("/api/v1/auth/login", json={"email": "qa-steward@example.com", "password": "local-qa-synthetic-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        items = client.get(f"/api/v1/mdm/runs/{run_id}/items/merge").json()
        assert items["total"] == 1
        endpoint = f"/api/v1/mdm/runs/{run_id}/items/merge/{items['items'][0]['id']}/explanation"
        first = client.post(endpoint)
        result["http_status"] = first.status_code
        if first.status_code != 200:
            result["provider_error_code"] = first.json().get("detail")
        assert first.status_code == 200 and first.json()["status"] == "READY"
        replay = client.post(endpoint)
        assert replay.status_code == 200 and replay.json() == first.json() and calls[0] == 1
        result.update(status="PASS", cache_replay_no_provider_call=True, db_cache_persisted=True,
                      model=first.json()["model_version"], usage=first.json()["usage"],
                      total_generation_requests_started=prior_requests + calls[0], output_text_omitted=True)
except Exception as error:
    result.update(status="FAIL", error_type=type(error).__name__, total_generation_requests_started=prior_requests + calls[0])
finally:
    save()
print(json.dumps(result))
raise SystemExit(result["status"] != "PASS")
