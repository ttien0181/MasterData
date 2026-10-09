"""Non-overlapping stage timing and best-effort process resource metadata."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")
STAGES = ("load", "fit", "normalize", "index", "candidate", "score", "route", "cluster", "evaluate", "report")


@dataclass(frozen=True)
class StageMeasurement:
    wall_seconds: float
    cpu_seconds: float
    item_count: int | None = None
    throughput_per_second: float | None = None


class ResourceTracker:
    def __init__(self) -> None:
        self.measurements: dict[str, StageMeasurement] = {}
        self._rss_before = peak_rss_bytes()
        self._started_wall = time.perf_counter()
        self._started_cpu = time.process_time()

    def measure(self, stage: str, function: Callable[[], T], *, item_count: int | None = None) -> T:
        if stage not in STAGES:
            raise ValueError(f"unknown runtime stage: {stage}")
        if stage in self.measurements:
            raise ValueError(f"runtime stage already measured (would double-count): {stage}")
        wall_start, cpu_start = time.perf_counter(), time.process_time()
        result = function()
        wall, cpu = time.perf_counter() - wall_start, time.process_time() - cpu_start
        throughput = item_count / wall if item_count is not None and wall > 0 else None
        self.measurements[stage] = StageMeasurement(wall, cpu, item_count, throughput)
        return result

    def snapshot(self) -> dict[str, object]:
        wall_total = time.perf_counter() - self._started_wall
        cpu_total = time.process_time() - self._started_cpu
        return {
            "stages": {name: {"wall_seconds": row.wall_seconds, "cpu_seconds": row.cpu_seconds,
                              "item_count": row.item_count, "throughput_per_second": row.throughput_per_second}
                       for name, row in self.measurements.items()},
            "measured_stage_wall_seconds_sum": sum(item.wall_seconds for item in self.measurements.values()),
            "end_to_end_wall_seconds": wall_total,
            "end_to_end_cpu_seconds": cpu_total,
            "unmeasured_stages": {name: "NOT_RUN_OR_NOT_INSTRUMENTED" for name in STAGES
                                  if name not in self.measurements},
            "process_peak_rss_bytes": peak_rss_bytes(),
            "process_peak_rss_delta_bytes": _rss_delta(self._rss_before, peak_rss_bytes()),
            "rss_method": _rss_method(),
            "note": "Stage wall times are non-overlapping calls; process peak RSS may include earlier process activity.",
        }


def _rss_delta(before: int | None, after: int | None) -> int | None:
    return max(0, after - before) if before is not None and after is not None else None


def _rss_method() -> str:
    try:
        import psutil  # type: ignore[import-not-found]
        return f"psutil:{psutil.__version__}"
    except ImportError:
        try:
            import resource
            return "resource.getrusage (platform-specific units)"
        except ImportError:
            return "unavailable"


def peak_rss_bytes() -> int | None:
    try:
        import psutil  # type: ignore[import-not-found]
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except ImportError:
        try:
            import resource
            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return value if os.name == "nt" else value * 1024
        except ImportError:
            return None
