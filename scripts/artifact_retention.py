"""Inspect artifacts and prune only known, disposable benchmark files.

The CLI is read-only unless --apply is supplied. Successful benchmark runners
call finish_run after disposing their SQLAlchemy engine and before manifesting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_PATTERNS = (
    "advanced-m0-20261014/e0-*",
    "advanced-m2a-20261014/pipeline-*",
    "advanced-m2b-20261014/experiments-*/*",
    "advanced-m2b-20261014/fixed-universe-*/*",
)
DB_NAMES = ("test.db", "test.db-wal", "test.db-shm", "test.db-journal")
WHEEL = "torch-2.14.1+cpu-cp311-cp311-manylinux_2_28_x86_64.whl"


def fs_path(path: Path) -> Path:
    """Use extended Windows paths without changing the paths in audit records."""
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def plain_path(path: Path) -> Path:
    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def checked_path(path: Path, root: Path) -> Path:
    """Reject traversal, symlinks and Windows junctions before every mutation."""
    root = root.resolve()
    path = path.absolute()
    if not path.is_relative_to(root / "artifacts") or path == root / "artifacts":
        raise ValueError(f"Outside artifacts: {path}")
    for part in (path, *path.parents):
        if part == root:
            break
        info = fs_path(part).lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Linked path is protected: {part}")
    if plain_path(fs_path(path).resolve()) != path:
        raise ValueError(f"Unexpected resolved path: {path}")
    return path


def tracked_files(root: Path) -> set[str]:
    if not (root / ".git").exists():
        return set()
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "artifacts"], cwd=root, check=True, capture_output=True,
    )
    return set(result.stdout.decode("utf-8").split("\0"))


def run_database_files(run: Path, root: Path) -> list[Path]:
    relative = run.relative_to(root / "artifacts").as_posix()
    if not any(Path(relative).match(pattern) for pattern in RUN_PATTERNS):
        raise ValueError(f"Not an owned benchmark run: {run}")
    return [checked_path(run / "state" / name, root) for name in DB_NAMES if (run / "state" / name).exists()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finish_run(run: Path, *, keep_state: bool = False, root: Path = ROOT) -> None:
    """Drop only this successful run's disposable DB; preserve inputs and evidence."""
    root = root.resolve()
    run = checked_path(run, root)
    report = checked_path(run / "report.json", root)
    json.loads(report.read_text(encoding="utf-8"))
    files = run_database_files(run, root)
    tracked = tracked_files(root)
    if any(p.relative_to(root).as_posix() in tracked for p in files):
        raise ValueError("Refusing to prune a tracked database")
    receipt = {
        "created_at": datetime.now(UTC).isoformat(),
        "policy": "keep-state" if keep_state else "prune-successful-benchmark-db",
        "resume_available": keep_state,
        "files": [{"path": p.relative_to(run).as_posix(), "bytes": p.stat().st_size} for p in files],
        "deleted": [],
    }
    target = run / "retention.json"
    if target.exists():
        checked_path(target, root)
    write_json(target, receipt)
    if not keep_state:
        for path in files:
            checked_path(path, root).unlink()
            receipt["deleted"].append(path.relative_to(run).as_posix())
            write_json(target, receipt)


def inventory(root: Path) -> tuple[list[Path], list[str]]:
    files, errors = [], []
    info = fs_path(root / "artifacts").lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Linked artifacts directory is protected")
    for directory, dirs, names in os.walk(fs_path(root / "artifacts"), onerror=lambda exc: errors.append(str(exc))):
        parent = plain_path(Path(directory))
        for name in dirs[:]:
            try:
                checked_path(parent / name, root)
            except (OSError, ValueError) as exc:
                dirs.remove(name)
                errors.append(str(exc))
        for name in names:
            try:
                path = parent / name
                info = fs_path(path).lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"Linked file is protected: {path}")
                files.append(path)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
    return files, errors


def candidates(root: Path, *, include_failed: bool, include_caches: bool) -> dict[Path, str]:
    selected = {}
    for pattern in RUN_PATTERNS:
        for run in (root / "artifacts").glob(pattern):
            checked_path(run, root)
            # A supervisor failure overrides an early report written before evaluation.
            supervisor = run / "supervisor.json"
            failed = (run / "failure.json").exists()
            if supervisor.exists():
                status = json.loads(checked_path(supervisor, root).read_text(encoding="utf-8"))
                failed |= status.get("exit_code") != 0 or bool(status.get("violation"))
            complete = (run / "manifest.json").is_file() and (run / "report.json").is_file()
            if not ((complete and not failed) or (include_failed and failed)):
                continue
            for path in run_database_files(run, root):
                selected[path] = "failed benchmark sandbox" if failed else "completed benchmark sandbox"
    if include_caches:
        for scope in ("cluster-improvements-20261013", "pair-cluster-review"):
            for path in (root / "artifacts" / scope).rglob("*.parquet.index.sqlite"):
                if Path(str(path).removesuffix(".index.sqlite")).is_file():
                    selected[checked_path(path, root)] = "rebuildable Parquet index"
        download = root / "artifacts/four-ai-20261015/encoder-download"
        source, installed = download / WHEEL, root / "backend/encoder/wheels" / WHEEL
        if source.is_file() and installed.is_file():
            checked_path(source, root)
            with source.open("rb") as a, installed.open("rb") as b:
                duplicate = hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest()
            if duplicate:
                selected[source] = "wheel identical to backend/encoder/wheels"
                for path in download.glob("part-*"):
                    if path.is_file() and path.name.removeprefix("part-").isdigit():
                        selected[checked_path(path, root)] = "download chunks; assembled wheel retained in backend"
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete the listed files; default is dry run")
    parser.add_argument("--min-age-days", type=float, default=7, help="Keep recently modified files (default: 7)")
    parser.add_argument("--include-failed", action="store_true", help="Also prune sandboxes with explicit failure evidence")
    parser.add_argument("--include-caches", action="store_true", help="Also prune rebuildable indexes and duplicate downloads")
    args = parser.parse_args()
    if args.min_age_days < 0:
        parser.error("--min-age-days must be nonnegative")
    root = ROOT.resolve()
    files, errors = inventory(root)
    total = sum(fs_path(p).stat().st_size for p in files)
    tracked = tracked_files(root)
    cutoff = time.time() - args.min_age_days * 86400
    selected = candidates(root, include_failed=args.include_failed, include_caches=args.include_caches)
    plan = []
    for path, reason in selected.items():
        info = path.stat()
        if info.st_mtime > cutoff or path.relative_to(root).as_posix() in tracked:
            continue
        plan.append({"path": path.relative_to(root).as_posix(), "bytes": info.st_size,
                     "mtime_ns": info.st_mtime_ns, "reason": reason})
    plan.sort(key=lambda item: item["bytes"], reverse=True)
    print(f"Readable artifacts: {total / 1e9:.3f} GB; {len(files)} files; scan issues: {len(errors)}")
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {sum(p['bytes'] for p in plan) / 1e9:.3f} GB; {len(plan)} files")
    for item in plan:
        if item["bytes"] > 10_000_000:
            print(f"  {item['bytes'] / 1e9:.3f} GB  {item['path']}")
    for error in errors[:10]:
        print(f"Scan issue: {error}")
    if len(errors) > 10:
        print(f"  ... {len(errors) - 10} more scan issues (saved in the apply receipt)")
    if not args.apply:
        return 0
    audit = root / "artifacts/cleanup"
    if audit.exists():
        checked_path(audit, root)
    else:
        audit.mkdir()
        checked_path(audit, root)
    destination = audit / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    receipt = {"created_at": datetime.now(UTC).isoformat(), "readable_bytes_before": total,
               "scan_issues": errors, "plan": plan, "deleted": [], "errors": []}
    write_json(destination, receipt)
    for item in plan:
        try:
            path = checked_path(root / item["path"], root)
            info = path.stat()
            if info.st_size != item["bytes"] or info.st_mtime_ns != item["mtime_ns"]:
                raise ValueError("File changed since planning")
            path.unlink()
            receipt["deleted"].append(item)
        except (OSError, ValueError) as exc:
            receipt["errors"].append({"path": item["path"], "error": str(exc)})
        write_json(destination, receipt)
    freed = sum(p["bytes"] for p in receipt["deleted"])
    print(f"Removed {freed / 1e9:.3f} GB. Receipt: {destination}")
    return 1 if receipt["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
