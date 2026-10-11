"""Combine API and worker JSONL steps for one resolution run.

Usage: python scripts/mdm_trace_report.py RUN_ID
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


def load_events(directory: Path) -> list[dict]:
    events = []
    for path in sorted(directory.glob("mdm-*.jsonl")):
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # A process may have stopped midway through its last write.
    return sorted(events, key=lambda item: item["ts"])


def render(run_id: str, events: list[dict]) -> str:
    sessions = {event["upload_session_id"] for event in events
                if event.get("run_id") == run_id and event.get("upload_session_id")}
    relevant = [event for event in events if event.get("run_id") == run_id
                or event.get("upload_session_id") in sessions]
    if not relevant:
        raise ValueError(f"No trace found for run {run_id}")
    summary = next((event for event in reversed(relevant)
                    if event["step"] == "run.summary" and event["event"] == "completed"), None)
    lines = [f"RUN {run_id}", f"UPLOAD SESSION {', '.join(sorted(sessions)) or 'none'}"]
    if summary:
        first_upload = next((event for event in relevant
                             if event["step"] == "upload.receive_part" and event["event"] == "started"), None)
        first = first_upload or relevant[0]
        elapsed = (datetime.fromisoformat(summary["ts"]) - datetime.fromisoformat(first["ts"])).total_seconds()
        lines.append(f"FROM FIRST OBSERVED STEP TO COMMITTED RESULT {elapsed:.3f}s")
        claim = next((event for event in relevant if event["step"] == "queue.claim"
                      and event["event"] == "completed"), None)
        if claim:
            lines.append(f"QUEUE WAIT {claim['queue_wait_ms'] / 1000:.3f}s")
        for key in ("branch", "record_count", "file_count", "candidate_count",
                    "insufficient_count", "pair_count", "review_pair_count",
                    "auto_pair_count", "auto_proposal_count", "auto_record_count",
                    "review_case_count", "blocked_pair_count"):
            lines.append(f"{key}: {summary.get(key, 'unknown')}")
    else:
        lines.append("RESULT INCOMPLETE: no committed run.summary event")
    lines.append("\nSTEPS (duration includes child steps when nested):")
    def identity(event):
        return event.get("span_id") or (
            event["step"], event["pid"], event.get("attempt_id"),
            event.get("file_id"), event.get("part_number"))

    pending: dict[object, list[dict]] = {}
    matched_starts = set()
    for event in relevant:
        key = identity(event)
        if event["event"] == "started":
            pending.setdefault(key, []).append(event)
        elif event["event"] in {"completed", "failed"} and pending.get(key):
            matched_starts.add(id(pending[key].pop(0)))
    for event in relevant:
        if event["step"] == "run.summary":
            continue
        if event["event"] in {"completed", "failed"}:
            duration = (f"{event['duration_ms']:>12.3f} ms" if "duration_ms" in event
                        else "           - ms")
            lines.append(f"{event['ts']} {event['step']:<34} {event['event']:<9} "
                         f"{duration} pid={event['pid']}")
        elif event["event"] == "started" and id(event) not in matched_starts:
            lines.append(f"{event['ts']} {event['step']:<34} INCOMPLETE")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--trace-dir", type=Path, default=Path("data/trace"))
    args = parser.parse_args()
    events = load_events(args.trace_dir)
    report = render(args.run_id, events)
    output = args.trace_dir / "reports" / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.txt").write_text(report, encoding="utf-8")
    sessions = {event["upload_session_id"] for event in events
                if event.get("run_id") == args.run_id and event.get("upload_session_id")}
    timeline = [event for event in events if event.get("run_id") == args.run_id
                or event.get("upload_session_id") in sessions]
    (output / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
