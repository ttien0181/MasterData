"""Owned SQLite spool: complete decision barrier, global order and indexed guards.

Payload changes only update triage; sort keys/observed decision are immutable.
Cursor queries fetch one row at a time, never an all-edge Python list.
"""
import json
import math
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

EDGE_ORDER = "weight DESC NULLS LAST, left_id ASC, right_id ASC"


def edge_key(pair):
    a, b = sorted((pair["left_id"], pair["right_id"]))
    weight = pair.get("match_weight")
    if weight is not None and not math.isfinite(weight):
        raise ValueError("Nonfinite cluster edge weight")
    return weight is None, -weight if weight is not None else 0, a, b


class DecisionStore:
    def __init__(self, directory=None):
        self.scratch = TemporaryDirectory(prefix="p119-decisions-", dir=directory)
        self.con = sqlite3.connect(str(Path(self.scratch.name) / "edges.db"))
        self.con.execute("PRAGMA cache_size=-4096")
        self.con.execute("PRAGMA temp_store=FILE")
        self.con.execute("CREATE TABLE edges (id TEXT PRIMARY KEY, left_id TEXT, right_id TEXT, weight REAL, decision TEXT, payload TEXT, UNIQUE(left_id,right_id))")
        self.con.execute("CREATE INDEX edge_order ON edges(weight DESC,left_id,right_id)")
        self.con.execute("CREATE TABLE checks (left_id TEXT, right_id TEXT, reason TEXT, PRIMARY KEY(left_id,right_id))")
        self.sealed = False

    def add(self, pair):
        if self.sealed:
            raise ValueError("Decision generation already sealed")
        edge_key(pair)
        pair = dict(pair)
        a, b = sorted((pair["left_id"], pair["right_id"]))
        if a == b:
            raise ValueError("Self edge cannot enter clustering")
        if pair["left_id"] != a:
            raise ValueError("Decision evidence must have canonical endpoint orientation")
        self.con.execute("INSERT INTO edges VALUES (?,?,?,?,?,?)", (pair["id"], a, b,
            pair.get("match_weight"), pair["decision"], json.dumps(pair)))

    def seal(self):
        self.con.commit()
        self.sealed = True

    def ordered(self, *, matches=False, review=False):
        if not self.sealed:
            raise ValueError("Complete decision barrier required before union")
        where = " WHERE decision='MERGEABLE'" if matches else ""
        cursor = self.con.execute("SELECT payload FROM edges" + where + " ORDER BY " + EDGE_ORDER)
        for row in cursor:
            pair = json.loads(row[0])
            if not review or pair.get("triage_status") == "REVIEW":
                yield pair

    def __iter__(self):
        return self.ordered()

    def __len__(self):
        return self.con.execute("SELECT count(*) FROM edges").fetchone()[0]

    def get(self, a, b):
        row = self.con.execute("SELECT payload FROM edges WHERE left_id=? AND right_id=?", tuple(sorted((a, b)))).fetchone()
        return json.loads(row[0]) if row else None

    def get_id(self, pair_id):
        row = self.con.execute("SELECT payload FROM edges WHERE id=?", (pair_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, pair):
        self.con.execute("UPDATE edges SET payload=? WHERE id=?", (json.dumps(pair), pair["id"]))

    def check(self, a, b):
        row = self.con.execute("SELECT reason FROM checks WHERE left_id=? AND right_id=?", (a, b)).fetchone()
        return row[0] if row else None

    def save_check(self, a, b, reason):
        self.con.execute("INSERT INTO checks VALUES (?,?,?)", (a, b, reason))

    def close(self):
        self.con.close()
        self.scratch.cleanup()

    def write_parquet(self, path, batch_size=2048):
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = pa.schema([("id", pa.string()), ("left_id", pa.string()),
            ("right_id", pa.string()), ("match_weight", pa.float64()),
            ("decision", pa.string()), ("decision_json", pa.string())])
        with pq.ParquetWriter(path, schema) as writer:
            rows = []
            for pair in self:
                rows.append({**{k: pair.get(k) for k in schema.names if k != "decision_json"},
                             "decision_json": json.dumps(pair)})
                if len(rows) == batch_size:
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    rows = []
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
