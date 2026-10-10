"""Aggregate baseline/final decision differences without exposing record values."""
import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

root = Path("artifacts/full-system-20261009")
results = []
for final in sorted((root / "stages-final").glob("TC*")):
    baseline = root / "stages-baseline" / final.name
    a, b = baseline / "qa-bench_decisions.parquet", final / "qa-bench_decisions.parquet"
    with duckdb.connect() as db:
        db.from_parquet(str(a)).create_view("qa_a")
        db.from_parquet(str(b)).create_view("qa_b")
        missing = db.execute("SELECT count(*) FROM qa_a FULL JOIN qa_b USING(id) WHERE qa_a.id IS NULL OR qa_b.id IS NULL").fetchone()[0]
        assert missing == 0
        count = db.execute("SELECT count(*) FROM qa_a").fetchone()[0]
        different = {}
        for column in pq.read_schema(a).names:
            name = '"' + column.replace('"', '""') + '"'
            changed = db.execute(f"SELECT count(*) FROM qa_a JOIN qa_b USING(id) WHERE qa_a.{name} IS DISTINCT FROM qa_b.{name}").fetchone()[0]
            if changed:
                different[column] = changed
    results.append({"dataset": final.name, "decision_rows": count, "changed_field_counts": dict(sorted(different.items())),
                    "scope": "observed aggregate difference, no row values/PII exported"})
(root / "final-decision-differences.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results))
