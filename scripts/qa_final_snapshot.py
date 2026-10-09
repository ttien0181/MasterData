"""Build/activate only QA backend/worker and save a secret-free runtime snapshot."""
import hashlib
import importlib.metadata
import json
import subprocess
import time

from qa_live_system import ROOT, Probe

output = ROOT / "artifacts/full-system-20261009/final-runtime"
output.mkdir(parents=True, exist_ok=False)
compose = ["docker", "compose", "--env-file", ".env.qa.local", "-p", "masterone-qa", "-f", "docker-compose.qa.yml"]
result = {}


def command(args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout


assert command([*compose, "config", "--quiet"]) == ""
started = time.monotonic()
with (output / "build.log").open("w", encoding="utf-8") as log:
    built = subprocess.run([*compose, "build", "backend"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
result["backend_build"] = {"exit_code": built.returncode, "seconds": time.monotonic() - started}
assert built.returncode == 0
with (output / "activation.log").open("w", encoding="utf-8") as log:
    active = subprocess.run([*compose, "up", "-d", "--no-deps", "backend", "worker"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=90)
assert active.returncode == 0
probe = Probe(output / "health")
probe.request("GET", "/ready")
modules = ["main", "config", "logging_config", "api.mdm_resolution", "mdm.pipeline.standardization",
           "mdm.pipeline.clustering", "mdm.pipeline.cluster_guards", "mdm.pipeline.stage_runner",
           "mdm.resolution_worker", "mdm.resolution_import", "mdm.gemini_explanation", "storage.object_store", "storage.r2"]
code = "import importlib.util,hashlib,json; modules=" + repr(modules) + "; print(json.dumps({n:hashlib.sha256(open(importlib.util.find_spec('masterone.'+n).origin,'rb').read()).hexdigest() for n in modules}))"
container = json.loads(command(["docker", "exec", "masterone-qa-backend-1", "python", "-c", code]))
host = {name: hashlib.sha256((ROOT / "backend/src/masterone" / (name.replace(".", "/") + ".py")).read_bytes()).hexdigest() for name in modules}
result["source_image_hashes"] = {name: {"host": host[name], "container": container[name], "equal": host[name] == container[name]} for name in modules}
assert all(row["equal"] for row in result["source_image_hashes"].values())
result["services"] = [json.loads(line) for line in command([*compose, "ps", "--format", "json"]).splitlines()]
packages = ["fastapi", "pydantic", "sqlalchemy", "psycopg", "boto3", "botocore", "duckdb", "pyarrow", "pandas", "scikit-learn", "alembic"]
result["host_versions"] = {p: importlib.metadata.version(p) for p in packages}
code = "import importlib.metadata,json; print(json.dumps({p:importlib.metadata.version(p) for p in " + repr(packages) + "}))"
result["container_versions"] = json.loads(command(["docker", "exec", "masterone-qa-backend-1", "python", "-c", code]))
code = "import json;from masterone.config import get_settings;s=get_settings();print(json.dumps({'app_env':s.app_env,'llm_enabled':s.llm_enabled,'semantic_address_enabled':s.semantic_address_enabled,'gemini_key_present':bool(s.gemini_api_key),'openrouter_key_present':bool(s.openrouter_api_key),'mdm_engine_v2_enabled':s.mdm_engine_v2_enabled,'pipeline_resume_enabled':s.pipeline_resume_enabled,'stream_decide_materialize_enabled':s.stream_decide_materialize_enabled}))"
result["runtime_flags"] = json.loads(command(["docker", "exec", "masterone-qa-backend-1", "python", "-c", code]))
result["resource_snapshot"] = command(["docker", "stats", "--no-stream", "--format", "{{json .}}", "masterone-qa-backend-1", "masterone-qa-worker-1", "masterone-qa-frontend-1", "masterone-qa-database-1", "masterone-qa-storage-1"])
result["status"] = "PASS"
(output / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
probe.client.close()
probe.engine.dispose()
print(json.dumps({"status": "PASS", "build": result["backend_build"], "source_modules_equal": len(modules), "services": len(result["services"])}))
