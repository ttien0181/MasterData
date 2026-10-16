"""Read-only verification of frozen M2-A evidence; writes a final receipt/manifest."""
from __future__ import annotations

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/advanced-m2a-20261014"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_files(base: Path, entries: list[dict]) -> int:
    for item in entries:
        path = (base / item["path"]).resolve()
        assert path.is_relative_to(base.resolve()), item["path"]
        assert path.stat().st_size == item["bytes"], str(path)
        assert digest(path) == item["sha256"], str(path)
    return len(entries)


def main() -> None:
    pipeline = OUT / "pipeline-tc1-01"
    frozen_files = verify_files(pipeline, read(pipeline / "manifest.json")["files"])
    config = read(pipeline / "config.json")
    for path, pin in {**config["code_hashes"], **config["source_hashes"]}.items():
        assert digest(ROOT / path) == pin, path
    model_root = OUT / "e5-trial-01"
    model = read(model_root / "model-manifest.json")
    model_files = verify_files(model_root / "model", model["files"])
    assert model["bundle_digest"] == "d9ffddf2ca5476f2c9ae023e921941c9db614a414e607c4a30bd82c7a88693d6"
    assert digest(model_root / "requirements-lock.txt") == "291fea99bb0805521e318cfd69d2a2aca9fcb7d7d6341d62ba92b37a9c0c7141"
    totals = {}
    suites = {
        "m2a_backend": (OUT / "contracts-tests-btbj8fmh/junit.xml", 39),
        "production_regression": (ROOT / "artifacts/advanced-m1-20261014/regression-7rbxypi6/junit.xml", 570),
    }
    for name, (path, expected) in suites.items():
        nodes = list(ET.parse(path).getroot().iter("testsuite"))
        counts = {key: sum(int(n.get(key, "0")) for n in nodes) for key in ("tests", "failures", "errors", "skipped")}
        assert counts == {"tests": expected, "failures": 0, "errors": 0, "skipped": 0}, counts
        totals[name] = counts
    frontend_bytes = (OUT / "frontend-tests-03.log").read_bytes()
    frontend_text = frontend_bytes.decode("utf-16" if frontend_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")
    assert "Tests  28 passed (28)" in frontend_text
    report = read(pipeline / "report.json")
    supervisor = read(pipeline / "supervisor.json")
    assert supervisor["exit_code"] == 0 and supervisor["violation"] is None
    assert report["labels_accessed"] is False and report["quality_metrics"] is None
    assert report["auto_merge_enabled"] is False
    assert report["agent_pairs"] == report["unique_pairs"] == report["candidate_pairs"] == 83127
    assert report["matching_agents"]["status"] == "OK"
    assert report["matching_agents"]["automation_allowed"] is False
    unchanged = []
    tracked = subprocess.check_output(["git", "ls-files", "backend/src/masterone/mdm/pipeline", "backend/src/masterone/agents", "docker-compose.yml"], cwd=ROOT).decode().splitlines()
    for rel in tracked:
        path = ROOT / rel
        original = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT)
        assert path.read_bytes().replace(b"\r\n", b"\n") == original.replace(b"\r\n", b"\n"), rel
        unchanged.append(rel)
    assert digest(ROOT / "docker-compose.yml") == "f5891727fdfcaf6d50579901036413654779b50caaab406cb05bf0fe6bc2ac81"
    config_text = (ROOT / "backend/src/masterone/config.py").read_text(encoding="utf-8")
    assert "advanced_matching_agents_enabled: bool = False" in config_text
    assert "embedding_runtime_root: str = ''" in config_text
    assert not any((ROOT / "backend/migrations/versions").glob("0034*"))
    receipt = {
        "status": "PASS_TECHNICAL_TRIAL_ONLY",
        "quality_selection": "BLOCKED_NO_VALID_DEV",
        "pipeline_manifest_files_verified": frozen_files,
        "model_files_verified": model_files,
        "code_pins_verified": len(config["code_hashes"]),
        "source_pins_verified": len(config["source_hashes"]),
        "tests": totals,
        "frontend_tests": 28,
        "unchanged_tracked_pipeline_chat_compose": unchanged,
        "compose_sha256": digest(ROOT / "docker-compose.yml"),
        "new_migration": False,
        "checkpoint": "STOP_M2A",
    }
    (OUT / "checkpoint-verification.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    paths = [p for p in (ROOT / "docs/advanced-matching").glob("m2a*.md")]
    paths += [ROOT / "docs/advanced-matching/decision-log.md", Path(__file__), OUT / "checkpoint-verification.json", OUT / "review-closure.md"]
    for folder in (pipeline, OUT / "contracts-tests-btbj8fmh", ROOT / "artifacts/advanced-m1-20261014/regression-7rbxypi6"):
        paths += [p for p in folder.iterdir() if p.is_file() and p.suffix in {".json", ".xml", ".log"}]
    paths += [OUT / "final-checks.json", OUT / "frontend-tests-03.log", OUT / "frontend-types-02.log", model_root / "model-manifest.json", model_root / "requirements-lock.txt", model_root / "inference-report.json", model_root / "supervisor-report.json"]
    paths += list((ROOT / "backend/src/masterone/mdm/matching_agents").glob("*.py"))
    entries = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(set(paths))]
    (OUT / "checkpoint-manifest.json").write_text(json.dumps({"root": str(ROOT), "files": entries}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
