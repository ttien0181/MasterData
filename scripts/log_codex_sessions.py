#!/usr/bin/env python3
"""Recover Codex user prompts from local session transcripts for this repo.

Codex command hooks are the live path. This scanner is a fallback for local
sessions whose hook was skipped; it runs before AI logs are submitted on push.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VN_TZ = timezone(timedelta(hours=7))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _same_workspace(session_cwd: str, repo_root: Path) -> bool:
    if not session_cwd:
        return False
    try:
        cwd = os.path.normcase(str(Path(session_cwd).resolve()))
        root = os.path.normcase(str(repo_root.resolve()))
        return os.path.commonpath((cwd, root)) == root
    except (OSError, ValueError):
        return False


def _existing_entries(log_dir: Path) -> tuple[set[str], set[tuple[str, str, str]]]:
    ids: set[str] = set()
    signatures: set[tuple[str, str, str]] = set()
    paths = [log_dir / "session.jsonl", *sorted((log_dir / "archive").glob("*.jsonl"))]
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", errors="replace") as source:
            for line in source:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict) or entry.get("tool") != "codex":
                    continue
                if entry.get("entry_id"):
                    ids.add(entry["entry_id"])
                signatures.add((
                    str(entry.get("session_id", "")),
                    str(entry.get("turn_id", "")),
                    str(entry.get("prompt", "")),
                ))
    return ids, signatures


def collect_sessions(
    sessions_dir: Path | None = None,
    log_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    root_text = _git("rev-parse", "--show-toplevel")
    origin = _git("remote", "get-url", "origin")
    if not root_text or not origin:
        return 0

    repo_root = Path(root_text)
    sessions_dir = sessions_dir or Path.home() / ".codex" / "sessions"
    log_dir = log_dir or Path(os.environ.get("AI_LOG_DIR", repo_root / ".ai-log"))
    if not sessions_dir.is_dir():
        return 0

    repo = origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    student = _git("config", "user.email")
    seen_ids, seen_signatures = _existing_entries(log_dir)
    new_entries: list[dict] = []

    for path in sorted(sessions_dir.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8-sig", errors="replace") as source:
                first_line = source.readline()
                meta = json.loads(first_line)
                if meta.get("type") != "session_meta":
                    continue
                session = meta.get("payload") or {}
                if not _same_workspace(str(session.get("cwd") or ""), repo_root):
                    continue
                session_id = str(session.get("id") or session.get("session_id") or "")
                git_info = session.get("git") or {}

                for line in source:
                    if '"response_item"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item = record.get("payload") or {}
                    if (record.get("type") != "response_item"
                            or item.get("type") != "message"
                            or item.get("role") != "user"):
                        continue
                    metadata = item.get("internal_chat_message_metadata_passthrough") or {}
                    kinds = metadata.get("content_item_kinds") or []
                    if kinds and "user.text" not in kinds:
                        continue
                    prompt = "\n".join(
                        block.get("text", "")
                        for block in (item.get("content") or [])
                        if isinstance(block, dict) and block.get("type") == "input_text"
                    ).strip()[:1000]
                    if not prompt or prompt.startswith((
                        "<environment_context>", "<external_codex_apps_open_page>"
                    )):
                        continue
                    entry_id = str(item.get("id") or "")
                    turn_id = str(metadata.get("turn_id") or "")
                    signature = (session_id, turn_id, prompt)
                    if entry_id in seen_ids or signature in seen_signatures:
                        continue

                    try:
                        timestamp = datetime.fromisoformat(
                            str(record["timestamp"]).replace("Z", "+00:00")
                        ).astimezone(VN_TZ).isoformat()
                    except (KeyError, ValueError):
                        timestamp = datetime.now(VN_TZ).isoformat()
                    entry = {
                        "ts": timestamp,
                        "tool": "codex",
                        "event": "UserPromptSubmit",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "entry_id": entry_id,
                        "model": "",
                        "repo": repo,
                        "branch": git_info.get("branch") or _git("rev-parse", "--abbrev-ref", "HEAD"),
                        "commit": str(git_info.get("commit_hash") or _git("rev-parse", "--short", "HEAD"))[:7],
                        "student": student,
                        "prompt": prompt,
                        "transcript_path": str(path),
                    }
                    new_entries.append(entry)
                    if entry_id:
                        seen_ids.add(entry_id)
                    seen_signatures.add(signature)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

    if new_entries and not dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "session.jsonl").open("a", encoding="utf-8") as output:
            for entry in sorted(new_entries, key=lambda item: item["ts"]):
                output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(new_entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count missing prompts without writing")
    parser.add_argument("--sessions-dir", type=Path, help="Override the local Codex sessions directory")
    arguments = parser.parse_args()
    count = collect_sessions(
        sessions_dir=arguments.sessions_dir, dry_run=arguments.dry_run
    )
    action = "found" if arguments.dry_run else "recovered"
    print(f"[ai-log] {action} {count} Codex prompt(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
