"""Run copied OneMaster reference tests without writing to its workspace.

Only the selected offline migration boundary/person suites are exercised.
Real sockets are forbidden, and TEST_DATABASE_URL is explicitly unset.
"""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from merge_shadow_validation import ROOT, file_hash, write
from run_merge_baseline import isolated_environment

SUITES = (
    "test_prepare_normalize_stream.py", "test_build_score_partition_stream.py",
    "test_score_decide_prep.py", "test_decide_materialize_partition.py",
    "test_generation_staging.py", "test_person_merge_v2.py", "test_person_case_e2e.py",
    "test_background_upload.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to(ROOT / "artifacts/merge-validation"):
        parser.error("Copy/test output must be inside artifacts/merge-validation")
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = directory / "snapshot"
    snapshot.mkdir()
    for folder in ("src", "backend", "tests", "configs", "scripts"):
        shutil.copytree(args.reference / folder, snapshot / folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env*", "*.db", "*.sqlite*", "node_modules", ".pytest_cache"))
    for name in ("alembic.ini", "pyproject.toml"):
        shutil.copy2(args.reference / name, snapshot / name)
    import yaml
    normalization = yaml.safe_load((snapshot / "configs/normalization.yaml").read_text(encoding="utf-8"))
    for item in normalization.get("reference", {}).values():
        if isinstance(item, dict) and "path" in item:
            relative = Path(item["path"])
            original = (args.reference / relative).resolve()
            if not original.is_relative_to((args.reference / "data/reference").resolve()):
                raise ValueError("Reference data escapes the declared mapping directory")
            (snapshot / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, snapshot / relative)
    hashes = {p.relative_to(snapshot).as_posix(): file_hash(p) for p in sorted(snapshot.rglob("*")) if p.is_file()}
    guard = directory / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket, sys\noriginal = socket.socket.connect\n"
        "def forbidden(*a, **k):\n    raise RuntimeError('OFFLINE_VALIDATION_NETWORK_FORBIDDEN')\n"
        "def connect(self, address):\n"
        "    if sys._getframe(1).f_code is socket.socketpair.__code__:\n"
        "        return original(self, address)\n"
        "    return forbidden()\n"
        "socket.socket.connect = connect\nsocket.socket.connect_ex = forbidden\nsocket.create_connection = forbidden\n",
        encoding="utf-8")
    env = isolated_environment(directory / "isolated")
    env.update({"PYTHONPATH": os.pathsep.join(map(str, (guard, snapshot, snapshot / "src", snapshot / "tests"))),
        "PYTHONDONTWRITEBYTECODE": "1", "TEST_DATABASE_URL": "", "DATABASE_URL": "",
        "R2_ENDPOINT": "", "REDIS_URL": "redis://127.0.0.1:9/0",
        "JWT_SECRET": "offline-validation-only-secret-32-characters", "WORK_DIR": str(directory / "work"),
        "GEMINI_API_KEY_1": "", "GEMINI_API_KEY_2": "", "GEMINI_API_KEY_3": "", "LLM_ENABLED": "false"})
    paths = ["tests/" + name for name in SUITES if (snapshot / "tests" / name).is_file()]
    # The reference does not implement Windows extended paths. Keep fixture
    # roots short while remaining entirely inside the authorized P-119 tree.
    scratch = ROOT / "sandbox"
    scratch.mkdir(exist_ok=True)
    pytest_root = Path(tempfile.mkdtemp(prefix="ov-", dir=scratch))
    command = [str(args.python.resolve()), "-X", "utf8", "-m", "pytest", "-p", "no:cacheprovider", "-q",
        "--tb=short", "--basetemp=" + str(pytest_root), "--junitxml=" + str(directory / "junit.xml"), *paths]
    print("OneMaster copied offline suites: " + str(directory), flush=True)
    with (directory / "pytest.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, cwd=snapshot, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = "TIMEOUT"
    unchanged = all(file_hash(args.reference / name) == value for name, value in hashes.items())
    write(directory / "execution.json", {"command": command, "exit_code": code, "reference_unchanged": unchanged,
        "source_hashes": hashes, "suites": paths, "network": "socket calls prohibited", "scope": "selected offline suites; not whole OneMaster certification"})
    print((directory / "pytest.log").read_text(encoding="utf-8")[-8000:])
    return code if isinstance(code, int) else 1


if __name__ == "__main__":
    raise SystemExit(main())
