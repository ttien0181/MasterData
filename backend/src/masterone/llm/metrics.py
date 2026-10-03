"""LLM observability metrics — in-process counters/gauges.

Tối thiểu cần theo dõi cho demo:
  llm_requests_total, llm_requests_by_type
  llm_cache_hit_rate
  llm_gate_rate (semantic_llm_pairs / candidate_pairs)
  llm_error_rate, llm_timeout_rate, llm_429_rate
  llm_latency_p50, llm_latency_p95
  llm_input_tokens_total, llm_output_tokens_total
  llm_cost_estimate (placeholder — cần giá model thực)
  semantic_fallback_count, semantic_result_distribution
  explanation_generated_count

Phase L4: in-process; Phase L5+: push to Prometheus/Grafana.
"""
from __future__ import annotations

import statistics
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMetrics:
    """Thread-safe in-process metrics store."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Request counters
    requests_total: int = 0
    requests_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    requests_by_status: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Latency samples (ms) — keep last 1000
    latency_samples: list[float] = field(default_factory=list)
    _LATENCY_WINDOW = 1000

    # Token / cost
    input_tokens_total: int = 0
    output_tokens_total: int = 0

    # Gate
    gate_total: int = 0
    gate_passed: int = 0

    # Cache
    cache_checks: int = 0
    cache_hits: int = 0

    # Semantic result distribution
    semantic_result_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Explanation
    explanation_generated: int = 0
    group_explanation_generated: int = 0

    # Error breakdown
    timeout_count: int = 0
    error_429_count: int = 0
    schema_error_count: int = 0

    # Budget
    budget_exceeded_count: int = 0

    def record_request(
        self,
        *,
        request_type: str,
        status: str,
        latency_ms: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        with self._lock:
            self.requests_total += 1
            self.requests_by_type[request_type] += 1
            self.requests_by_status[status] += 1
            self.input_tokens_total += input_tokens
            self.output_tokens_total += output_tokens
            self.latency_samples.append(latency_ms)
            if len(self.latency_samples) > self._LATENCY_WINDOW:
                self.latency_samples = self.latency_samples[-self._LATENCY_WINDOW:]
            if status == "TIMEOUT":
                self.timeout_count += 1
            if status == "LLM_ERROR_429":
                self.error_429_count += 1
            if status == "SCHEMA_ERROR":
                self.schema_error_count += 1

    def record_gate(self, *, passed: bool) -> None:
        with self._lock:
            self.gate_total += 1
            if passed:
                self.gate_passed += 1

    def record_cache(self, *, hit: bool) -> None:
        with self._lock:
            self.cache_checks += 1
            if hit:
                self.cache_hits += 1

    def record_semantic_result(self, result: str) -> None:
        with self._lock:
            self.semantic_result_counts[result] += 1

    def record_explanation_generated(self, *, group: bool = False) -> None:
        with self._lock:
            if group:
                self.group_explanation_generated += 1
            else:
                self.explanation_generated += 1

    def record_budget_exceeded(self, count: int = 1) -> None:
        with self._lock:
            self.budget_exceeded_count += count

    # ─── Derived metrics ──────────────────────────────────────────────────────

    def gate_rate(self) -> float:
        """Tỷ lệ pairs gọi LLM / tổng pairs qua gate."""
        with self._lock:
            if self.gate_total == 0:
                return 0.0
            return self.gate_passed / self.gate_total

    def cache_hit_rate(self) -> float:
        with self._lock:
            if self.cache_checks == 0:
                return 0.0
            return self.cache_hits / self.cache_checks

    def error_rate(self) -> float:
        with self._lock:
            if self.requests_total == 0:
                return 0.0
            errors = sum(
                v for k, v in self.requests_by_status.items()
                if k not in ("SUCCESS", "CACHED", "SKIPPED_BY_GATE")
            )
            return errors / self.requests_total

    def latency_p50(self) -> float | None:
        with self._lock:
            if not self.latency_samples:
                return None
            return statistics.median(self.latency_samples)

    def latency_p95(self) -> float | None:
        with self._lock:
            if not self.latency_samples:
                return None
            sorted_samples = sorted(self.latency_samples)
            idx = int(len(sorted_samples) * 0.95)
            return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def snapshot(self) -> dict[str, Any]:
        """Dashboard-ready dict — không expose internal state."""
        with self._lock:
            # Compute derived metrics inline (không gọi methods có lock riêng → tránh deadlock)
            gate_rate_pct = round(
                (self.gate_passed / self.gate_total * 100) if self.gate_total else 0.0, 2
            )
            cache_hit_rate_pct = round(
                (self.cache_hits / self.cache_checks * 100) if self.cache_checks else 0.0, 2
            )
            if self.requests_total:
                errors = sum(
                    v for k, v in self.requests_by_status.items()
                    if k not in ("SUCCESS", "CACHED", "SKIPPED_BY_GATE")
                )
                error_rate_pct = round(errors / self.requests_total * 100, 2)
            else:
                error_rate_pct = 0.0
            if self.latency_samples:
                p50 = statistics.median(self.latency_samples)
                sorted_s = sorted(self.latency_samples)
                idx = int(len(sorted_s) * 0.95)
                p95 = sorted_s[min(idx, len(sorted_s) - 1)]
            else:
                p50 = p95 = None
            return {
                "requests_total": self.requests_total,
                "requests_by_type": dict(self.requests_by_type),
                "gate_total": self.gate_total,
                "gate_passed": self.gate_passed,
                "gate_rate_pct": gate_rate_pct,
                "cache_checks": self.cache_checks,
                "cache_hits": self.cache_hits,
                "cache_hit_rate_pct": cache_hit_rate_pct,
                "error_rate_pct": error_rate_pct,
                "timeout_count": self.timeout_count,
                "error_429_count": self.error_429_count,
                "schema_error_count": self.schema_error_count,
                "budget_exceeded_count": self.budget_exceeded_count,
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
                "input_tokens_total": self.input_tokens_total,
                "output_tokens_total": self.output_tokens_total,
                "semantic_result_distribution": dict(self.semantic_result_counts),
                "explanation_generated": self.explanation_generated,
                "group_explanation_generated": self.group_explanation_generated,
            }

    def reset(self) -> None:
        """Dùng trong tests."""
        with self._lock:
            self.requests_total = 0
            self.requests_by_type = defaultdict(int)
            self.requests_by_status = defaultdict(int)
            self.latency_samples = []
            self.input_tokens_total = 0
            self.output_tokens_total = 0
            self.gate_total = 0
            self.gate_passed = 0
            self.cache_checks = 0
            self.cache_hits = 0
            self.semantic_result_counts = defaultdict(int)
            self.explanation_generated = 0
            self.group_explanation_generated = 0
            self.timeout_count = 0
            self.error_429_count = 0
            self.schema_error_count = 0
            self.budget_exceeded_count = 0


# ─── Singleton ────────────────────────────────────────────────────────────────

_metrics = LLMMetrics()


def get_metrics() -> LLMMetrics:
    return _metrics


def reset_metrics() -> None:
    """Dùng trong tests."""
    _metrics.reset()
