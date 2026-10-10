"""Frozen scoring identity, full-snapshot census and complete batched artifacts.

SCORE emits all candidates, including hard conflicts and insufficient evidence.
DECIDE is a separate consumer and uses the existing rule policy, never an
invented probability threshold. Splink is opt-in with an evaluated frozen model.
"""

import json
import math
import os
import platform
import tempfile
from collections import Counter
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.metadata import version
from itertools import islice
from pathlib import Path
from time import perf_counter

import pyarrow as pa
import pyarrow.parquet as pq

from masterone.mdm.pipeline import matching, policy_engine

SCORE_PARALLEL_MIN = 15000  # expected candidates; below this SCORE stays in-process
CONTRACT_VERSION = "p119_score_v1"
RULE_VERSION = "p119_rule_v1"
NORMALIZATION_VERSION = "p119_normalized_v1"
SCORE_SCHEMA = pa.schema([
    ("left_id", pa.string()), ("right_id", pa.string()), ("blocking_reasons", pa.list_(pa.string())),
    ("rule_score", pa.float64()), ("raw_rule_score", pa.float64()),
    ("match_probability", pa.float64()), ("splink_match_weight", pa.float64()),
    ("evidence_json", pa.string()), ("model_version", pa.string()), ("model_hash", pa.string()),
    ("config_hash", pa.string()),
])


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=lambda v: sorted(v) if isinstance(v, (set, frozenset)) else str(v))


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def sql_ident(value):
    return '"' + value.replace('"', '""') + '"'


def decode_record(record):
    for key in ("normalized_fields", "raw_fields", "quality_flags"):
        if isinstance(record.get(key), str):
            record[key] = json.loads(record[key] or "{}")
    record.setdefault("raw_fields", {})
    return record


class MemorySnapshot:
    def __init__(self, records):
        self.records = {}
        for r in records:
            rid = r["id"]
            if not isinstance(rid, str) or not rid or rid in self.records:
                raise ValueError("Scoring snapshot requires distinct nonempty IDs")
            self.records[rid] = r

    def iter_records(self, batch_size):
        for rid in sorted(self.records):
            yield self.records[rid]

    def get_records(self, ids):
        return {rid: self.records[rid] for rid in ids if rid in self.records}


class ParquetSnapshot:
    """Batch hydration via disk-backed DuckDB; never load all candidate records."""
    def __init__(self, path, connection):
        self.con = connection
        self.path = str(path)
        rows, unique, invalid = self.con.execute(f"SELECT count(*), count(DISTINCT id), count(*) FILTER (WHERE id IS NULL OR id='') FROM read_parquet({sql_literal(path)})").fetchone()
        if rows != unique or invalid:
            raise ValueError("Scoring snapshot requires distinct nonempty IDs")

    def iter_records(self, batch_size):
        cursor = self.con.cursor()
        try:
            cursor.execute(f"SELECT * FROM read_parquet({sql_literal(self.path)}) ORDER BY id")
            names = [d[0] for d in cursor.description]
            while rows := cursor.fetchmany(batch_size):
                for row in rows:
                    yield decode_record(dict(zip(names, row, strict=True)))
        finally:
            cursor.close()

    def get_records(self, ids):
        self.con.register("scoring_ids", pa.table({"id": sorted(ids)}))
        try:
            cursor = self.con.execute(f"SELECT s.* FROM read_parquet({sql_literal(self.path)}) s JOIN scoring_ids i USING(id) ORDER BY s.id")
            names = [d[0] for d in cursor.description]
            return {record["id"]: record for row in cursor.fetchall()
                    for record in [decode_record(dict(zip(names, row, strict=True)))]}
        finally:
            self.con.unregister("scoring_ids")


@dataclass(frozen=True)
class ScoringConfig:
    mode: str = "rule"
    batch_size: int = 2048
    duckdb_memory_limit_mb: int = 256
    normalization_version: str = NORMALIZATION_VERSION
    model_path: str | None = None
    manifest_path: str | None = None
    model_version: str | None = None
    model_sha256: str | None = None

    def __post_init__(self):
        if self.mode not in {"rule", "splink"}:
            raise ValueError("Scoring mode must be rule or splink")
        if type(self.batch_size) is not int or self.batch_size < 1:
            raise ValueError("Scoring batch size must be positive")
        if type(self.duckdb_memory_limit_mb) is not int or self.duckdb_memory_limit_mb < 1:
            raise ValueError("DuckDB memory limit must be positive")
        if self.normalization_version != NORMALIZATION_VERSION:
            raise ValueError("Unsupported scoring normalization contract")
        if self.mode == "rule" and any((self.model_path, self.manifest_path, self.model_version, self.model_sha256)):
            raise ValueError("Rule scorer cannot use a Splink artifact identity")


@dataclass(frozen=True)
class ScoreResult:
    left_id: str
    right_id: str
    blocking_reasons: tuple[str, ...]
    rule_score: float
    raw_rule_score: float
    match_probability: float | None
    splink_match_weight: float | None
    evidence_json: str
    model_version: str
    model_hash: str
    config_hash: str

    @classmethod
    def from_row(cls, row):
        return cls(**{**row, "blocking_reasons": tuple(row["blocking_reasons"])})


class ScoringCoverageError(RuntimeError):
    pass


class ScoringInputChangedError(RuntimeError):
    pass


class SplinkModelBlockedError(RuntimeError):
    pass


def load_frozen_model(config):
    if not all((config.model_path, config.manifest_path, config.model_sha256, config.model_version)):
        raise SplinkModelBlockedError("Splink inactive: requires trained/calibrated model, evaluated manifest and pinned version/hash; no automatic training")
    try:
        model_bytes = Path(config.model_path).read_bytes()
        manifest_bytes = Path(config.manifest_path).read_bytes()
        model, manifest = json.loads(model_bytes), json.loads(manifest_bytes)
    except (OSError, ValueError) as error:
        raise SplinkModelBlockedError("Splink inactive: model/manifest missing or invalid JSON") from error
    if not isinstance(model, dict) or not isinstance(manifest, dict):
        raise SplinkModelBlockedError("Splink inactive: model/manifest must be JSON objects")
    digest = sha256(model_bytes).hexdigest()
    required = {"trained": True, "calibrated": True, "production_approved": True,
                "model_version": config.model_version, "model_sha256": digest,
                "normalization_version": config.normalization_version, "splink_version": "5.0.0"}
    if digest != config.model_sha256 or any(manifest.get(k) != v for k, v in required.items()) or any(
        manifest.get(k) is not True for k in ("trained", "calibrated", "production_approved")
    ):
        raise SplinkModelBlockedError("Splink inactive: model identity/evaluation manifest mismatch")
    if not all(isinstance(manifest.get(k), str) and len(manifest[k]) == 64 and
               all(c in "0123456789abcdef" for c in manifest[k])
               for k in ("training_snapshot_sha256", "evaluation_report_sha256")):
        raise SplinkModelBlockedError("Splink inactive: missing training/evaluation provenance")
    if model.get("link_type") != "dedupe_only" or model.get("unique_id_column_name") != "record_id":
        raise SplinkModelBlockedError("Splink inactive: only record_id dedupe_only artifacts supported")
    fields = manifest.get("fields")
    if not isinstance(fields, dict) or not fields or any(f not in matching.FIELDS + matching.OPTIONAL_FIELDS or f != path for f, path in fields.items()):
        raise SplinkModelBlockedError("Splink inactive: incompatible normalized field projection")
    if version("splink") != "5.0.0":
        raise SplinkModelBlockedError("Splink adapter requires pinned Splink 5.0.0")
    prior = model.get("probability_two_random_records_match")
    if not isinstance(prior, (int, float)) or not 0 < prior < 1 or not model.get("comparisons"):
        raise SplinkModelBlockedError("Splink inactive: missing fitted prior/comparisons")
    for comparison in model["comparisons"]:
        levels = [level for level in comparison.get("comparison_levels", []) if not level.get("is_null_level")]
        if not levels or any(not isinstance(level.get(k), (int, float)) or not 0 < level[k] < 1 for level in levels for k in ("m_probability", "u_probability")):
            raise SplinkModelBlockedError("Splink inactive: unfitted/invalid m/u parameters")
        if any(abs(sum(level[k] for level in levels) - 1) > 1e-6 for k in ("m_probability", "u_probability")):
            raise SplinkModelBlockedError("Splink inactive: invalid m/u distributions")
    return model, manifest, sha256(manifest_bytes).hexdigest()


class SplinkPredictor:
    """Splink 5 public registered-pair API, bounded prediction batches.

    Caller supplies an already validated frozen model. No training or score
    threshold here. Linker is recreated for each batch to avoid prediction cache
    reuse; transient SQL tables are dropped. Full snapshot stays the TF corpus.
    """
    def __init__(self, connection, input_table, model):
        self.con, self.input_table, self.model = connection, input_table, deepcopy(model)

    def predict(self, pairs):
        from splink import DuckDBAPI, Linker
        if version("splink") != "5.0.0":
            raise SplinkModelBlockedError("Splink adapter requires 5.0.0")
        before = {row[0] for row in self.con.execute("SHOW TABLES").fetchall()}
        api = DuckDBAPI(connection=self.con)
        settings = deepcopy(self.model)
        settings.update(retain_matching_columns=False, retain_intermediate_calculation_columns=False)
        table = pa.table({"join_key_l": [p[0] for p in pairs], "join_key_r": [p[1] for p in pairs],
                          "match_key": ["0"] * len(pairs)})
        try:
            linker = Linker(api.register(self.input_table), settings)
            blocked = api.register(table)
            linker.table_management.register_blocked_pairs_for_predict(blocked)
            predictions = linker.inference.predict()
            rows = self.con.execute(f"SELECT record_id_l, record_id_r, match_probability, match_weight FROM {sql_ident(predictions.physical_name)}").fetchall()
            result = {}
            for left, right, probability, weight in rows:
                if (left, right) in result:
                    raise ScoringCoverageError("Duplicate Splink prediction")
                if probability is None or not math.isfinite(probability) or not 0 <= probability <= 1 or weight is None or not math.isfinite(weight):
                    raise ScoringCoverageError("Invalid Splink numeric output")
                result[(left, right)] = (probability, weight)
            if set(result) != set(pairs):
                raise ScoringCoverageError("Splink prediction coverage differs from candidate batch")
            return result
        finally:
            after = self.con.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='main'").fetchall()
            for name, kind in sorted(after, key=lambda row: (row[1] != "VIEW", row[0])):
                if name not in before:
                    self.con.execute(f"DROP {'VIEW' if kind == 'VIEW' else 'TABLE'} IF EXISTS {sql_ident(name)}")


def _score_rule_batch(candidates, records, frequencies, negative_pairs, identity):
    """Pure rule-mode scoring of one hydrated batch; runs in a worker process.
    Must stay byte-identical to the rule branch of ScoringEngine.score_batch."""
    results = []
    for c in candidates:
        left, right = c["left_id"], c["right_id"]
        reasons = tuple(sorted(set(c.get("reasons", c.get("blocking_reasons", [])))))
        evidence = matching.compare_and_score_rule(records[left], records[right], set(reasons),
                                                   frequencies, negative_pairs)
        score = evidence.pop("score")
        raw_score = evidence.pop("_raw_weight", score)
        evidence.pop("match_weight")
        results.append(ScoreResult(left_id=left, right_id=right, blocking_reasons=reasons,
            rule_score=score, raw_rule_score=raw_score, match_probability=None, splink_match_weight=None,
            evidence_json=canonical_json(evidence), model_version=identity["model_version"],
            model_hash=identity["model_hash"], config_hash=identity["config_hash"]))
    return results


def _decide_rule(result, records, policies):
    evidence = {**json.loads(result.evidence_json), "score": result.rule_score,
                "match_weight": result.rule_score, "_raw_weight": result.raw_rule_score}
    payload = matching.decide_rule_result(evidence, records[result.left_id],
        records[result.right_id], set(result.blocking_reasons), policies=policies)
    return {**payload, "left_id": result.left_id, "right_id": result.right_id,
            "rule_score": result.rule_score, "match_probability": result.match_probability,
            "splink_match_weight": result.splink_match_weight,
            "scoring_model_version": result.model_version, "scoring_model_hash": result.model_hash,
            "scoring_config_hash": result.config_hash}


def _decide_rule_batch(results, records, policies):
    """Worker-side DECIDE for one batch; shares _decide_rule with the serial path."""
    started = perf_counter()
    decisions = [_decide_rule(r, records, policies) for r in results]
    return decisions, (perf_counter() - started) * 1000


class ScoringEngine:
    def __init__(self, snapshot, config=None, *, negative_pairs=None):
        self.snapshot, self.config = snapshot, config or ScoringConfig()
        from masterone.mdm.pipeline.decision_semantics import enabled
        self.decision_semantics_v2 = enabled()
        self.negative_pairs = frozenset(tuple(sorted(pair)) for pair in (negative_pairs or ()))
        self.policies = deepcopy(policy_engine.get_policies())
        source_dir = Path(matching.__file__).parent
        sources = {name: file_hash(source_dir / name) for name in (
            "matching.py", "standardization.py", "hard_constraints.py", "evidence_floor.py", "policy_engine.py", "decision_semantics.py")}
        self.rule_hash = sha256(canonical_json(sources).encode()).hexdigest()
        self.rule_sources = {source_dir / name: digest for name, digest in sources.items()}
        self.identity = {"contract_version": CONTRACT_VERSION, "mode": self.config.mode,
            "decision_semantics_v2": self.decision_semantics_v2,
            "model_version": RULE_VERSION, "model_hash": self.rule_hash, "rule_baseline_hash": self.rule_hash,
            "normalization_version": self.config.normalization_version, "config_hash": sha256(canonical_json(asdict(self.config)).encode()).hexdigest(),
            "runtime": {k: version(k) for k in ("rapidfuzz", "duckdb", "pyarrow")}, "python": platform.python_version(),
            "policy_hash": sha256(canonical_json({k: asdict(v) for k, v in self.policies.items()}).encode()).hexdigest(),
            "negative_pairs_hash": sha256(canonical_json(sorted(self.negative_pairs)).encode()).hexdigest()}
        self.model = self.manifest = None
        if self.config.mode == "splink":
            self.model, self.manifest, manifest_hash = load_frozen_model(self.config)
            self.model, self.manifest = deepcopy(self.model), deepcopy(self.manifest)
            self.identity.update(model_version=self.config.model_version, model_hash=self.config.model_sha256,
                                 manifest_hash=manifest_hash, splink_version=version("splink"))
        self.frequencies, count, snapshot_hash = self._census()
        self.identity.update(snapshot_hash=snapshot_hash, snapshot_records=count,
                             frequency_hash=sha256(canonical_json(self.frequencies).encode()).hexdigest(),
                             frequency_scope="full_normalized_snapshot_scalar_phone_email_record_counts")
        self.predictor = None

    def _census(self):
        frequencies = {"phone": Counter(), "email": Counter()}
        digest = sha256()
        count = 0
        for r in self.snapshot.iter_records(self.config.batch_size):
            count += 1
            payload = {k: r.get(k, "UNKNOWN" if k == "entity_type" else {} if k.endswith("fields") else "")
                       for k in ("id", "source_system", "entity_type", "raw_fields", "normalized_fields")}
            digest.update((canonical_json(payload) + "\n").encode())
            for f in frequencies:
                if value := r["normalized_fields"].get(f, ""):
                    frequencies[f][value] += 1
        return frequencies, count, digest.hexdigest()

    def _hydrate_batch(self, candidates):
        if len(candidates) > self.config.batch_size:
            raise ScoringCoverageError("Scoring batch exceeds configured size")
        if self.config.mode == "splink" and self.predictor is None:
            raise SplinkModelBlockedError("Splink batch scorer not initialized; use write_scores")
        keys = [(c["left_id"], c["right_id"]) for c in candidates]
        if any(not isinstance(a, str) or not isinstance(b, str) or not a or a >= b for a, b in keys) or len(set(keys)) != len(keys):
            raise ScoringCoverageError("Candidate batch must contain distinct canonical nonself pairs")
        records = self.snapshot.get_records({rid for pair in keys for rid in pair})
        if any(a not in records or b not in records for a, b in keys):
            raise ScoringCoverageError("Candidate endpoint missing from normalized snapshot")
        return keys, records

    def _scored_batches(self, iterator, provider, expected_count):
        """Yield (batch, scored) in input order. Rule mode fans out to worker processes
        for large inputs; hydration, validation and every coverage check stay in this process."""
        workers = int(os.environ.get("MDM_SCORE_WORKERS", min(os.cpu_count() or 1, 4)))
        if (provider is not None or self.config.mode != "rule" or workers <= 1
                or expected_count < int(os.environ.get("MDM_SCORE_PARALLEL_MIN", SCORE_PARALLEL_MIN))):
            while batch := list(islice(iterator, self.config.batch_size)):
                yield batch, (self.score_batch(batch) if provider is None else provider(batch))
            return
        from collections import deque
        from concurrent.futures import ProcessPoolExecutor
        self._check_decision_semantics()
        pending = deque()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for batch in iter(lambda: list(islice(iterator, self.config.batch_size)), []):
                _, records = self._hydrate_batch(batch)
                pending.append((batch, pool.submit(_score_rule_batch, batch, records, self.frequencies,
                                                   self.negative_pairs, self.identity)))
                if len(pending) > 2 * workers:
                    done, future = pending.popleft()
                    yield done, future.result()
            while pending:
                done, future = pending.popleft()
                yield done, future.result()

    def score_batch(self, candidates):
        self._check_decision_semantics()
        keys, records = self._hydrate_batch(candidates)
        probabilities = self.predictor.predict(keys) if self.predictor else {}
        results = []
        for c in candidates:
            left, right = c["left_id"], c["right_id"]
            reasons = tuple(sorted(set(c.get("reasons", c.get("blocking_reasons", [])))))
            evidence = matching.compare_and_score_rule(records[left], records[right], set(reasons),
                                                       self.frequencies, self.negative_pairs)
            probability, weight = probabilities.get((left, right), (None, None))
            score = evidence.pop("score")
            raw_score = evidence.pop("_raw_weight", score)
            evidence.pop("match_weight")
            results.append(ScoreResult(left_id=left, right_id=right, blocking_reasons=reasons,
                rule_score=score, raw_rule_score=raw_score, match_probability=probability, splink_match_weight=weight,
                evidence_json=canonical_json(evidence), model_version=self.identity["model_version"],
                model_hash=self.identity["model_hash"], config_hash=self.identity["config_hash"]))
        return results

    def write_scores(self, candidates, output_path, *, expected_count, _score_provider=None,
                     _disk_guard=None, _max_output_bytes=None):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output.with_suffix(".score.json")
        if output.exists():
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ScoringInputChangedError("Existing score artifact lacks valid pinned metadata") from error
            pins = ("model_hash", "model_version", "config_hash", "snapshot_hash", "policy_hash", "negative_pairs_hash", "runtime", "python", "decision_semantics_v2")
            if any(previous.get(k) != self.identity[k] for k in pins) or previous.get("artifact_sha256") != file_hash(output):
                raise ScoringInputChangedError("Existing score artifact identity changed; use a new run artifact")
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".partial.parquet", delete=False) as handle:
            temporary = Path(handle.name)
        count = batches = max_rows = 0
        digest = sha256()
        # On-disk uniqueness/coverage ledger avoids an O(pair count) Python set.
        import sqlite3
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".coverage.db", delete=False) as handle:
            ledger_path = Path(handle.name)
        ledger = sqlite3.connect(ledger_path)
        try:
            with _disk_guard(16384) if _disk_guard else nullcontext():
                ledger.execute("CREATE TABLE pairs (left_id TEXT, right_id TEXT, PRIMARY KEY(left_id, right_id))")
            self._prepare_splink()
            iterator = iter(candidates)
            with ExitStack() as stack:
                sink = temporary
                if _disk_guard is not None:
                    from masterone.storage.artifact import _LimitedSink
                    temporary.unlink()
                    sink = stack.enter_context(_LimitedSink(temporary, _max_output_bytes, _disk_guard))
                writer = stack.enter_context(pq.ParquetWriter(sink, SCORE_SCHEMA, compression="zstd"))
                for batch, scored in self._scored_batches(iterator, _score_provider, expected_count):
                    try:
                        # Conservative room for SQLite B-tree pages + rollback
                        # journal before inserting into the coverage ledger.
                        reserve = 32768 + sum(5*(len(c["left_id"].encode()) + len(c["right_id"].encode()) + 256) for c in batch)
                        with _disk_guard(reserve) if _disk_guard else nullcontext():
                            ledger.executemany("INSERT INTO pairs VALUES (?, ?)", [(c["left_id"], c["right_id"]) for c in batch])
                            ledger.commit()
                    except sqlite3.IntegrityError as error:
                        raise ScoringCoverageError("Duplicate candidate pair across batches") from error
                    # Streaming supplies canonical, already-scored partitions;
                    # all existing coverage/model/census checks still run here.
                    expected = {(c["left_id"], c["right_id"]) for c in batch}
                    actual = {(r.left_id, r.right_id) for r in scored}
                    if len(scored) != len(batch) or actual != expected:
                        raise ScoringCoverageError("Missing, duplicate or unexpected scored pair")
                    rows = [asdict(r) for r in scored]
                    for r, row in zip(scored, rows):
                        if (not math.isfinite(r.rule_score) or not math.isfinite(r.raw_rule_score) or r.model_hash != self.identity["model_hash"]
                            or r.model_version != self.identity["model_version"] or r.config_hash != self.identity["config_hash"]):
                            raise ScoringCoverageError("Invalid score/model identity")
                        if self.config.mode == "rule" and (r.match_probability is not None or r.splink_match_weight is not None):
                            raise ScoringCoverageError("Rule score cannot be presented as probability")
                        if self.config.mode == "splink" and (r.match_probability is None or not math.isfinite(r.match_probability)
                            or not 0 <= r.match_probability <= 1 or r.splink_match_weight is None or not math.isfinite(r.splink_match_weight)):
                            raise ScoringCoverageError("Invalid Splink score/probability")
                        digest.update((canonical_json(row) + "\n").encode())
                    writer.write_table(pa.Table.from_pylist(rows, schema=SCORE_SCHEMA))
                    count += len(scored)
                    batches += 1
                    max_rows = max(max_rows, len(scored))
            if count != expected_count or pq.ParquetFile(temporary).metadata.num_rows != expected_count:
                raise ScoringCoverageError("Scored artifact count differs from input candidate count")
            frequencies, records, snapshot_hash = self._census()
            self._check_decision_semantics()
            if snapshot_hash != self.identity["snapshot_hash"] or records != self.identity["snapshot_records"] or frequencies != self.frequencies:
                raise ScoringInputChangedError("Normalized snapshot changed during scoring")
            if any(file_hash(path) != digest for path, digest in self.rule_sources.items()):
                raise ScoringInputChangedError("Rule implementation changed during scoring")
            metrics = {**self.identity, "input_pairs": expected_count, "scored_pairs": count,
                       "output_batches": batches, "max_batch_rows": max_rows, "scores_hash": digest.hexdigest(),
                       "artifact_sha256": file_hash(temporary), "coverage": "complete",
                       "configuration": {k: v for k, v in asdict(self.config).items() if k not in {"model_path", "manifest_path"}}}
            # Validate before atomically replacing a previously complete artifact.
            os.replace(temporary, output)
            if _disk_guard is not None:
                from masterone.storage.artifact import atomic_json
                atomic_json(manifest_path, metrics, _disk_guard)
            else:
                manifest_path.write_text(canonical_json(metrics), encoding="utf-8")
            return metrics
        finally:
            ledger.close()
            ledger_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            self._close_splink()

    def _prepare_splink(self):
        if self.model is None:
            return
        import duckdb
        self.splink_scratch = tempfile.TemporaryDirectory(prefix="p119-splink-")
        self.splink_con = duckdb.connect(str(Path(self.splink_scratch.name) / "score.duckdb"))
        self.splink_con.execute(f"SET memory_limit='{self.config.duckdb_memory_limit_mb}MB'")
        self.splink_con.execute("SET threads=1")
        fields = sorted(self.manifest["fields"])
        schema = pa.schema([("record_id", pa.string())] + [(f, pa.string()) for f in fields])
        self.splink_con.execute("CREATE TABLE splink_snapshot (record_id VARCHAR," + ",".join(f"{sql_ident(f)} VARCHAR" for f in fields) + ")")
        records = iter(self.snapshot.iter_records(self.config.batch_size))
        while batch := list(islice(records, self.config.batch_size)):
            table = pa.Table.from_pylist([{"record_id": r["id"], **{f: r["normalized_fields"].get(f) or None for f in fields}} for r in batch], schema=schema)
            self.splink_con.register("snapshot_batch", table)
            self.splink_con.execute("INSERT INTO splink_snapshot SELECT * FROM snapshot_batch")
            self.splink_con.unregister("snapshot_batch")
        self.predictor = SplinkPredictor(self.splink_con, "splink_snapshot", self.model)

    def _close_splink(self):
        if hasattr(self, "splink_con"):
            self.splink_con.close()
            del self.splink_con
            self.splink_scratch.cleanup()
        self.predictor = None

    def decide(self, result, records):
        """Existing policy uses rule_score only; probabilities are shadow outputs."""
        self._check_decision_semantics()
        return _decide_rule(result, records, self.policies)

    def decided_batches(self, batches, expected_count, *, timings=None):
        """Yield (batch, hydrated, decisions) in input order. Large rule-mode inputs decide
        in worker processes; hydration and the semantics check stay in this process."""
        workers = int(os.environ.get("MDM_SCORE_WORKERS", min(os.cpu_count() or 1, 4)))
        if self.config.mode != "rule" or workers <= 1 or expected_count < int(
                os.environ.get("MDM_SCORE_PARALLEL_MIN", SCORE_PARALLEL_MIN)):
            for batch in batches:
                started = perf_counter()
                hydrated = self.snapshot.get_records({rid for r in batch for rid in (r.left_id, r.right_id)})
                if timings is not None:
                    timings["hydrate_ms"] = timings.get("hydrate_ms", 0.0) + (perf_counter() - started) * 1000
                started = perf_counter()
                decisions = [self.decide(r, hydrated) for r in batch]
                if timings is not None:
                    timings["policy_compute_ms"] = timings.get("policy_compute_ms", 0.0) + (perf_counter() - started) * 1000
                yield batch, hydrated, decisions
            return
        from collections import deque
        from concurrent.futures import ProcessPoolExecutor
        pending = deque()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for batch in batches:
                self._check_decision_semantics()
                started = perf_counter()
                hydrated = self.snapshot.get_records({rid for r in batch for rid in (r.left_id, r.right_id)})
                if timings is not None:
                    timings["hydrate_ms"] = timings.get("hydrate_ms", 0.0) + (perf_counter() - started) * 1000
                started = perf_counter()
                pending.append((batch, hydrated, pool.submit(_decide_rule_batch, batch, hydrated, self.policies)))
                if timings is not None:
                    timings["submit_ms"] = timings.get("submit_ms", 0.0) + (perf_counter() - started) * 1000
                if len(pending) > 2 * workers:
                    yield_batch, yield_hydrated, future = pending.popleft()
                    started = perf_counter()
                    decisions, compute_ms = future.result()
                    if timings is not None:
                        timings["worker_wait_ms"] = timings.get("worker_wait_ms", 0.0) + (perf_counter() - started) * 1000
                        timings["policy_compute_ms"] = timings.get("policy_compute_ms", 0.0) + compute_ms
                    yield yield_batch, yield_hydrated, decisions
            while pending:
                yield_batch, yield_hydrated, future = pending.popleft()
                started = perf_counter()
                decisions, compute_ms = future.result()
                if timings is not None:
                    timings["worker_wait_ms"] = timings.get("worker_wait_ms", 0.0) + (perf_counter() - started) * 1000
                    timings["policy_compute_ms"] = timings.get("policy_compute_ms", 0.0) + compute_ms
                yield yield_batch, yield_hydrated, decisions

    def _check_decision_semantics(self):
        from masterone.mdm.pipeline.decision_semantics import enabled
        if enabled() != self.decision_semantics_v2:
            raise ScoringInputChangedError("Decision semantics changed during this run")


def iter_score_batches(path, batch_size):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        yield [ScoreResult.from_row(row) for row in batch.to_pylist()]


def timed_score_batches(path, batch_size, timings):
    """Accumulate SCORE artifact read/decode time without per-batch log writes."""
    iterator = iter(iter_score_batches(path, batch_size))
    while True:
        started = perf_counter()
        try:
            batch = next(iterator)
        except StopIteration:
            break
        timings["score_read_ms"] = timings.get("score_read_ms", 0.0) + (perf_counter() - started) * 1000
        timings["batch_count"] = timings.get("batch_count", 0) + 1
        yield batch
