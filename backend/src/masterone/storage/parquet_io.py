"""Parquet I/O kept behind an adapter so stages do not depend on its API."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def write_parquet_atomic(rows: list[dict[str, Any]], path: str | Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    with NamedTemporaryFile(suffix=".parquet", dir=target.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        pq.write_table(table, temporary_path, compression="zstd", compression_level=3)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_parquet(path: str | Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()
