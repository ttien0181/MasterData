"""Circuit Breaker — bảo vệ pipeline khi OpenRouter gặp sự cố.

States:
  CLOSED  — hoạt động bình thường
  OPEN    — tạm thời không gọi LLM (circuit đang mở)
  HALF_OPEN — đang thử recover (chưa implement trong MVP)

Demo minimal:
  5 consecutive failures → OPEN 60s
  Sau 60s → tự động CLOSED lại (one-shot retry)

LLM subsystem check circuit trước mỗi call.
Circuit mở → raise LLMCircuitOpenError ngay (không thực sự gọi API).
"""
from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


class LLMCircuitOpenError(Exception):
    """Circuit đang OPEN — không gọi LLM."""


class CircuitBreaker:
    """Thread-safe circuit breaker cho LLM calls.

    Args:
        failure_threshold: số consecutive failures để mở circuit
        recovery_timeout_seconds: thời gian OPEN trước khi tự reset
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._effective_state()

    def _effective_state(self) -> CircuitState:
        """Tự động reset nếu recovery timeout đã hết (gọi trong lock)."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = CircuitState.CLOSED
                self._consecutive_failures = 0
                self._opened_at = None
        return self._state

    def check(self) -> None:
        """Raise LLMCircuitOpenError nếu circuit OPEN.
        Gọi trước mỗi LLM request.
        """
        with self._lock:
            if self._effective_state() == CircuitState.OPEN:
                remaining = 0.0
                if self._opened_at is not None:
                    remaining = max(
                        0.0,
                        self._recovery_timeout - (time.monotonic() - self._opened_at),
                    )
                raise LLMCircuitOpenError(
                    f"LLM circuit OPEN — retry in {remaining:.0f}s. "
                    "Core pipeline tiếp tục mà không có LLM."
                )

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        """Force reset — dùng trong tests."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    def stats(self) -> dict:
        with self._lock:
            return {
                "state": self._effective_state().value,
                "consecutive_failures": self._consecutive_failures,
                "threshold": self._threshold,
                "recovery_timeout_seconds": self._recovery_timeout,
            }


# ─── Singleton ────────────────────────────────────────────────────────────────

_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60.0)


def get_circuit_breaker() -> CircuitBreaker:
    return _breaker


def reset_circuit_breaker() -> None:
    """Dùng trong tests."""
    _breaker.reset()
