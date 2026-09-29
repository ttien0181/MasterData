"""Compatibility entry point for the fair Splink holdout benchmark.

Run from the repository root with ``python test_algorithm/splink.py``.
For the complete comparison, run ``python test_algorithm/benchmark_all.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != SCRIPT_DIR]
sys.path.insert(0, str(REPO_ROOT))

from test_algorithm.benchmark_splink import main  # noqa: E402

if __name__ == "__main__":
    main()
