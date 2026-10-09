"""Stable entry point for the benchmark-owned standalone package."""
from __future__ import annotations

import sys
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from standalone.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
