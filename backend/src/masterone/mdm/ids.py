"""Time-ordered UUIDs (RFC 9562 v7 layout) for high-volume row keys.

Random UUIDv4 primary keys scatter every insert across the B-tree. On tables
larger than shared_buffers that turns each batch into random disk reads (a
10k-row commit took ~4 minutes on a 3M-row table). Time-ordered keys append
to the right edge of the index instead.
"""

import os
import threading
import time
from uuid import UUID

_lock = threading.Lock()
_last_ms = -1
_counter = 0
_COUNTER_MASK = (1 << 62) - 1


def new_ordered_uuid() -> str:
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _counter = int.from_bytes(os.urandom(8), "big") & (_COUNTER_MASK >> 1)
        else:
            # Same or clock-skewed millisecond: keep strictly increasing order.
            _counter += 1
        counter = _counter & _COUNTER_MASK
        rand_a = (counter >> 62) & 0xFFF
        rand_b = counter & _COUNTER_MASK
        value = ((_last_ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(UUID(int=value))
