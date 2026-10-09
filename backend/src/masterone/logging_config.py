"""Structured JSON logging (§XC-OBS-006, TASK-7.2).

Usage:
    from masterone.logging_config import configure_logging, get_logger

    configure_logging()   # call once at app startup
    log = get_logger(__name__)
    log.info("stage_complete", extra={"run_id": "abc", "stage": "SCORE", "pairs": 120})

Output format (one JSON object per line):
    {"ts": "2026-10-08T06:00:00Z", "level": "INFO", "logger": "...",
     "message": "stage_complete", "run_id": "abc", "stage": "SCORE", "pairs": 120}

PII filter automatically masks phone/email values before they reach the log sink.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

_PHONE_RE = re.compile(r"\b0\d{9}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _mask_pii(text: str) -> str:
    text = _PHONE_RE.sub(lambda m: "***" + m.group()[-3:], text)
    text = _EMAIL_RE.sub(lambda m: m.group().split("@")[0][:1] + "***@" + m.group().split("@")[1], text)
    return text


class _PIIFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Render lazy args before masking; args may contain the PII.
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = "LOG_FORMAT_ERROR"
        record.msg = _mask_pii(rendered)
        record.args = ()
        return True


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote well-known structured fields from record.__dict__
        for key in ("run_id", "stage", "event", "pair_id", "record_id", "user_id", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            # SQL/provider exception strings may contain params or credentials.
            import traceback
            payload["error_type"] = record.exc_info[0].__name__
            payload["frames"] = [{"function": frame.name, "line": frame.lineno}
                                 for frame in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO, json_output: bool = True) -> None:
    """Configure root logger with structured JSON output and PII filter.

    Call once at application startup (main.py / lifespan).
    """
    handler = logging.StreamHandler(sys.stdout)
    handler._masterone_handler = True
    if json_output:
        handler.setFormatter(_JSONFormatter())
    handler.addFilter(_PIIFilter())

    root = logging.getLogger()
    root.setLevel(level)
    # Replace only our handler; retain host/test capture handlers.
    for existing in list(root.handlers):
        if getattr(existing, "_masterone_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger for *name*. configure_logging() must have been called first."""
    return logging.getLogger(name)
