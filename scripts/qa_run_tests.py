"""Run targeted pytest paths with the same safe isolation as the QA baseline."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from run_merge_baseline import isolated_environment

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("paths", nargs="+")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
started = time.monotonic()
command = [sys.executable, "-X", "utf8", "-m", "pytest", *args.paths, "-p", "pytest_asyncio.plugin", "-q",
           "--junitxml=" + str(args.output / "results.xml")]
with (args.output / "pytest.log").open("w", encoding="utf-8") as log:
    result = subprocess.run(command, env=isolated_environment(args.output / "state"),
                            stdout=log, stderr=subprocess.STDOUT, timeout=900)
(args.output / "execution.json").write_text(json.dumps({"exit_code": result.returncode,
    "seconds": time.monotonic() - started, "paths": args.paths}), encoding="utf-8")
print((args.output / "pytest.log").read_text(encoding="utf-8"), flush=True)
raise SystemExit(result.returncode)
