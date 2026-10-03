"""Retry logic — exponential backoff với jitter.

Chỉ retry với:
  429, 5xx, timeout/network transient, malformed structured output (1 lần)
Không retry vô hạn. Max = LLMSettings.max_retries (default 2).
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

# Backoff delays giây theo thứ tự retry 1, 2, ... (sau đó dùng giá trị cuối)
_BACKOFF_SECONDS = [0.5, 1.5]

# HTTP status codes được phép retry
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


def _jitter(base: float) -> float:
    return base + random.uniform(0.0, base * 0.3)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    retryable_exc: tuple[type[BaseException], ...] = (),
) -> T:
    """Gọi fn() với retry + jitter backoff.

    Args:
        fn: coroutine factory, gọi không có argument.
        max_retries: số lần retry tối đa (0 = không retry).
        retryable_exc: exception types được phép retry.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_retries:
                break
            if retryable_exc and not isinstance(exc, retryable_exc):
                break
            delay = _jitter(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
