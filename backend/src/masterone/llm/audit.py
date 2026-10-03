"""LLM audit — ghi record mỗi request/response để reproducibility.

Mỗi LLM call phải lưu:
  model, prompt_version, schema_version, request_hash, response_hash,
  status, latency_ms, input_tokens, output_tokens, error_code

Phase L1: in-memory log.
Phase L4: flush_to_db() ghi batch vào DB table llm_requests (non-blocking).
          Không flush raw PII — chỉ hash + metadata.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from masterone.llm.contracts import LLMRequestStatus


@dataclass
class LLMAuditRecord:
    request_id: str
    request_type: str          # "semantic_fallback" | "pair_explanation" | "group_explanation"
    model: str
    provider: str
    prompt_version: str
    schema_version: str
    request_hash: str
    status: LLMRequestStatus
    pair_id: str | None = None
    response_hash: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def new_request_id() -> str:
    return str(uuid.uuid4())


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_request(payload: Any) -> str:
    return _hash(payload)


def hash_response(payload: Any) -> str:
    return _hash(payload)


# ─── In-memory sink (Phase L1) ────────────────────────────────────────────────
# Phase L4 thay bằng DB write.

_audit_log: list[LLMAuditRecord] = []


def record(audit: LLMAuditRecord) -> None:
    """Lưu audit record. Phase L1 = in-memory; Phase L4 = DB."""
    _audit_log.append(audit)


def get_log() -> list[LLMAuditRecord]:
    """Trả về bản copy — chỉ dùng cho tests / debug."""
    return list(_audit_log)


def clear_log() -> None:
    """Dùng trong tests để reset state."""
    _audit_log.clear()


# ─── DB flush (Phase L4) ──────────────────────────────────────────────────────

def flush_to_db(session_factory: Any) -> int:
    """Flush pending in-memory audit records vào DB.

    Gọi từ background task / lifespan shutdown — KHÔNG block request path.
    Trả về số records đã flush.

    Args:
        session_factory: SQLAlchemy sessionmaker / callable → Session
    """
    if not _audit_log:
        return 0

    from masterone.mdm.models import LLMRequest

    records_to_flush = list(_audit_log)
    _audit_log.clear()

    flushed = 0
    try:
        with session_factory() as session:
            for rec in records_to_flush:
                row = LLMRequest(
                    id=rec.request_id,
                    request_type=rec.request_type,
                    pair_id=rec.pair_id,
                    model=rec.model,
                    provider=rec.provider,
                    prompt_version=rec.prompt_version,
                    schema_version=rec.schema_version,
                    request_hash=rec.request_hash,
                    response_hash=rec.response_hash,
                    status=rec.status.value if hasattr(rec.status, "value") else rec.status,
                    latency_ms=rec.latency_ms,
                    input_tokens=rec.input_tokens,
                    output_tokens=rec.output_tokens,
                    error_code=rec.error_code,
                    created_at=rec.created_at,
                )
                session.add(row)
                flushed += 1
            session.commit()
    except Exception:  # noqa: BLE001
        # Flush failure không được kill core pipeline
        import logging
        logging.getLogger(__name__).exception(
            "LLM audit flush failed — %d records lost", len(records_to_flush)
        )
    return flushed
