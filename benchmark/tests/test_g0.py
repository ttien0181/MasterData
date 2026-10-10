from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[1]
ROOT_RUN = BENCHMARK / "run.py"


def invoke(*args: str, cwd: Path, script: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["TMP"] = str(BENCHMARK / ".cache")
    env["TEMP"] = str(BENCHMARK / ".cache")
    env["TMPDIR"] = str(BENCHMARK / ".cache")
    return subprocess.run([sys.executable, script or str(ROOT_RUN), *args], cwd=cwd, env=env,
                          text=True, capture_output=True, timeout=20)


def test_standalone_import_does_not_import_application() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import sys, standalone; assert not any(m == 'src' or m.startswith('src.') or m == 'app' or m.startswith('app.') for m in sys.modules)"],
        cwd=BENCHMARK, text=True, capture_output=True, timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
    )
    assert result.returncode == 0, result.stderr


def test_dataset_files_and_dataset_code_are_separate() -> None:
    assert (BENCHMARK / "datasets" / "masterone_test" / "records.csv").is_file()
    assert (BENCHMARK / "standalone" / "datasets" / "__init__.py").is_file()
    legacy_data = BENCHMARK / "standalone" / "data"
    assert not legacy_data.exists() or not list(legacy_data.rglob("*.csv"))


def test_readmes_document_only_implemented_cli_surface() -> None:
    main_readme = (BENCHMARK / "README.md").read_text(encoding="utf-8")
    standalone_readme = (BENCHMARK / "standalone" / "README.md").read_text(encoding="utf-8")
    assert "python benchmark/run.py --help" in main_readme
    assert "python benchmark/datasets/download_febrl.py --dataset all" not in main_readme
    assert "former `datasets/download_febrl.py` is absent" in main_readme
    assert "python run.py validate --config standalone/config/experiments/example.json" in standalone_readme
    assert "B0 exact/rule" in standalone_readme


@pytest.mark.parametrize(
    ("cwd", "script"),
    [(BENCHMARK.parent, "benchmark/run.py"), (BENCHMARK, "run.py"),
     (BENCHMARK / ".cache", str(ROOT_RUN))],
)
def test_help_is_cwd_independent(cwd: Path, script: str) -> None:
    result = invoke("--help", cwd=cwd, script=script)
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("datasets", "validate", "run", "suite", "ablate", "compare"))


def test_run_rejects_missing_required_scope_arguments() -> None:
    result = invoke("run", cwd=BENCHMARK)
    assert result.returncode == 2
    assert "required" in result.stderr


def test_datasets_command_reports_registry_status_without_results() -> None:
    result = invoke("datasets", cwd=BENCHMARK)
    assert result.returncode == 0
    assert "masterone_test: adapter and audit available; train/dev and verified entity gold blocked" in result.stdout
    assert "exact: implemented B0 exact/rule pair baseline" in result.stdout
    assert "weighted: implemented Weighted V1 reimplementation" in result.stdout
    assert "masterone: registered; full MasterOne system implementation pending" in result.stdout


def test_config_validation_reports_malformed_and_unknown_system() -> None:
    bad = BENCHMARK / ".cache" / "bad-g0-config.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"stage":"bogus","system":"unknown"}', encoding="utf-8")
    result = invoke("validate", "--config", str(bad), cwd=BENCHMARK)
    assert result.returncode == 2
    assert "CONFIG ERROR" in result.stderr and "unknown stage" in result.stderr
    bad.write_text('{"stage":"blocking","system":"unknown"}', encoding="utf-8")
    result = invoke("validate", "--config", str(bad), cwd=BENCHMARK)
    assert result.returncode == 2
    assert "unknown system name" in result.stderr
    bad.write_text('{"stage":"blocking","system":"masterone","parameters":{"mystery":1}}', encoding="utf-8")
    result = invoke("validate", "--config", str(bad), cwd=BENCHMARK)
    assert result.returncode == 2
    assert "unknown parameter" in result.stderr
    bad.unlink()


def test_valid_config_works_with_unicode_workspace_path() -> None:
    config = BENCHMARK / ".cache" / "valid-g0-config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"stage":"blocking","system":"masterone","dataset":"masterone_test"}', encoding="utf-8")
    try:
        result = invoke("validate", "--config", str(config), cwd=BENCHMARK)
        assert result.returncode == 0, result.stderr
        assert "Execution was not performed" in result.stdout
    finally:
        config.unlink(missing_ok=True)


def test_documented_example_config_validates() -> None:
    result = invoke("validate", "--config", "standalone/config/experiments/example.json",
                    cwd=BENCHMARK, script="run.py")
    assert result.returncode == 0, result.stderr
    assert "Execution was not performed" in result.stdout
    result = invoke("validate", "--config", "standalone/config/experiments/example.json",
                    cwd=BENCHMARK.parent, script="benchmark/run.py")
    assert result.returncode == 0, result.stderr


def test_path_guard_rejects_traversal_and_outside_paths() -> None:
    from standalone.runtime.paths import PathSafetyError, output_path, resolve_benchmark_path

    with pytest.raises(PathSafetyError, match="traversal"):
        resolve_benchmark_path(BENCHMARK / "results" / ".." / "escape.txt")
    with pytest.raises(PathSafetyError, match="inside benchmark"):
        resolve_benchmark_path(BENCHMARK.parent / "outside.txt")
    with pytest.raises(PathSafetyError, match="traversal"):
        output_path("..", "outside.txt")
    with pytest.raises(PathSafetyError, match="inside benchmark"):
        output_path(str(BENCHMARK.parent / "outside.txt"))


def test_path_guard_resolves_symlink_escape() -> None:
    from standalone.runtime.paths import PathSafetyError, resolve_benchmark_path
    import subprocess

    link = BENCHMARK / ".cache" / "g0-escape-link"
    link.parent.mkdir(parents=True, exist_ok=True)
    created_as_junction = False
    try:
        link.symlink_to(BENCHMARK.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name == "nt":
            created = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "New-Item -ItemType Junction -Path $env:G0_LINK_PATH -Target $env:G0_TARGET_PATH | Out-Null"],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "G0_LINK_PATH": str(link), "G0_TARGET_PATH": str(BENCHMARK.parent)},
            )
            created_as_junction = created.returncode == 0
        if not created_as_junction:
            detail = created.stderr.strip() if os.name == "nt" else str("symlink API unavailable")
            pytest.skip(f"symlink/junction creation unavailable: {detail or 'permissions or filesystem support'}")
    try:
        with pytest.raises(PathSafetyError, match="inside benchmark"):
            resolve_benchmark_path(link / "outside.txt")
    finally:
        if created_as_junction:
            os.rmdir(link)  # Removes the junction itself without traversing its target.
        else:
            link.unlink(missing_ok=True)
        assert not link.exists() and not link.is_symlink()


def test_optional_dependencies_are_not_needed_for_core_help() -> None:
    code = (
        "import builtins, runpy, sys; "
        "blocked={'splink','torch','transformers','pyspark','dedupe','lightgbm'}; "
        "orig=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: (_ for _ in ()).throw(AssertionError('optional import: '+name)) if name.split('.')[0] in blocked else orig(name,*a,**k); "
        "sys.argv=['run.py','--help']; "
        f"runpy.run_path({str(ROOT_RUN)!r}, run_name='__main__')"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=BENCHMARK.parent.parent,
                            text=True, capture_output=True, timeout=20,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
    assert result.returncode == 0, result.stderr
    assert "usage: benchmark" in result.stdout
