"""Complete score coverage, full census and frozen identity across run modes."""

import json
from dataclasses import replace
from hashlib import sha256
from itertools import combinations

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from masterone.mdm.pipeline.candidate_engine import CandidateConfig
from masterone.mdm.pipeline.matching import score_pair, value_frequencies
from masterone.mdm.pipeline.scoring_engine import (
    MemorySnapshot,
    ParquetSnapshot,
    ScoringConfig,
    ScoringCoverageError,
    ScoringEngine,
    ScoringInputChangedError,
    SplinkModelBlockedError,
    SplinkPredictor,
    iter_score_batches,
    load_frozen_model,
)
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.resolution_service import _compute


def record(rid, **fields):
    return {"id": rid, "external_id": rid, "source_system": "CRM", "entity_type": "PERSON",
            "raw_fields": dict(fields), "quality_flags": [], "normalized_fields": fields}


def full_snapshot():
    fields = {"name": "nguyen van an", "phone": "0901234567", "email": "a@example.test",
        "phones": [{"value": "0901234567", "quality": "VALID"}],
        "emails": [{"value": "a@example.test", "quality": "VALID"}],
        "_field_quality": {"phone": "VALID", "email": "VALID"}}
    return [record("a", **fields), record("b", **fields)] + [
        record(f"z{i}", phone="0901234567") for i in range(5)]


def write_snapshot(path, records):
    pq.write_table(pa.Table.from_pylist([{**r, **{k: json.dumps(r[k]) for k in ("raw_fields", "normalized_fields", "quality_flags")}}
                                        for r in records]), path)


def candidate(a="a", b="b"):
    return {"left_id": a, "right_id": b, "reasons": ["exact_email", "exact_name"]}


@pytest.mark.parametrize("batch_size", [1, 2, 100])
def test_full_snapshot_frequency_small_large_and_rule_baseline(tmp_path, batch_size):
    records = full_snapshot()
    snapshot_path = tmp_path / "normalized.parquet"
    write_snapshot(snapshot_path, list(reversed(records)))
    config = ScoringConfig(batch_size=batch_size)
    small = ScoringEngine(MemorySnapshot(records), config)
    with duckdb.connect(str(tmp_path / "score.duckdb")) as con:
        large = ScoringEngine(ParquetSnapshot(snapshot_path, con), config)
        assert small.identity == large.identity
        assert small.frequencies == large.frequencies == value_frequencies(records)
        assert small.frequencies["phone"]["0901234567"] == 7
        score = small.score_batch([candidate()])[0]
        assert large.score_batch([candidate()]) == [score]
        baseline = score_pair(records[0], records[1], set(candidate()["reasons"]), value_frequencies(records))
        decided = small.decide(score, small.snapshot.records)
        for key, value in baseline.items():
            assert decided[key] == value
        evidence = json.loads(score.evidence_json)
        assert next(e for e in evidence["field_evidence"] if e["field"] == "phone")["state"] == "SH"
        assert score.rule_score == baseline["score"]
        assert score.match_probability is None and score.splink_match_weight is None
        assert "decision" not in evidence and "policy_id" not in evidence
        assert "score" not in evidence and "match_weight" not in evidence


def test_full_pipeline_small_large_scoring_outputs(tmp_path):
    records = full_snapshot()
    config = ScoringConfig(batch_size=1)
    candidates = CandidateConfig(include_tfidf=False, include_grams=False, composites=())
    small = _compute(records, "same", "person-v3", candidate_config=candidates,
                     scoring_config=config, scoring_output_dir=tmp_path / "small")
    runner = StageRunner("same", "a1", output_dir=tmp_path / "large")
    try:
        large = runner.run_all(records, candidate_config=candidates, scoring_config=config)
        assert pq.read_table(large["scoring_artifact"]).to_pylist() == pq.read_table(small["scoring_artifact"]).to_pylist()
        assert small["scoring_metrics"] == large["scoring_metrics"]
        assert small["pairs"] == large["pairs"]
        assert small["insufficient_count"] == large["insufficient_count"]
        assert large["scoring_metrics"]["scored_pairs"] == large["candidate_count"]
    finally:
        runner.con.close()


def test_score_artifact_retains_insufficient_and_hard_negative_pairs(tmp_path):
    records = [record("a", name="nguyen van an"), record("b", name="nguyen van an"),
               record("c", name="nguyen van an")]
    engine = ScoringEngine(MemorySnapshot(records), ScoringConfig(batch_size=1), negative_pairs=frozenset({("a", "c")}))
    path = tmp_path / "scores.parquet"
    metrics = engine.write_scores([candidate(a, b) for a, b in combinations("abc", 2)], path, expected_count=3)
    results = [r for batch in iter_score_batches(path, 1) for r in batch]
    assert len(results) == 3 and metrics["scored_pairs"] == 3
    assert engine.decide(results[0], engine.snapshot.records)["decision"] == "INSUFFICIENT_EVIDENCE"
    assert engine.decide(results[1], engine.snapshot.records)["decision"] == "NOT_MERGEABLE"
    assert "decision" not in pq.read_table(path).column_names
    assert pq.ParquetFile(path).num_row_groups == 3


def test_streaming_batch_memory_bound_and_no_pair_loss(tmp_path):
    records = [record(f"r{i:03}", name="nguyen van an") for i in range(100)]
    class TrackingSnapshot(MemorySnapshot):
        max_hydrated = 0
        def get_records(self, ids):
            self.max_hydrated = max(self.max_hydrated, len(ids))
            return super().get_records(ids)
    snapshot = TrackingSnapshot(records)
    engine = ScoringEngine(snapshot, ScoringConfig(batch_size=17))
    path = tmp_path / "scores.parquet"
    metrics = engine.write_scores((candidate(a["id"], b["id"]) for a, b in combinations(records, 2)),
                                  path, expected_count=4950)
    assert metrics["scored_pairs"] == 4950
    assert metrics["max_batch_rows"] == 17 and metrics["output_batches"] == 292
    assert snapshot.max_hydrated <= 34
    assert pq.ParquetFile(path).metadata.num_rows == 4950


@pytest.mark.parametrize("problem", ["dropped", "extra", "duplicate", "endpoint", "reverse", "count"])
def test_coverage_error_preserves_previous_complete_artifact(tmp_path, monkeypatch, problem):
    records = full_snapshot()
    engine = ScoringEngine(MemorySnapshot(records), ScoringConfig(batch_size=1))
    path = tmp_path / "scores.parquet"
    engine.write_scores([candidate()], path, expected_count=1)
    original = path.read_bytes()
    original_batch = engine.score_batch
    pairs, count = [candidate()], 1
    if problem == "dropped":
        monkeypatch.setattr(engine, "score_batch", lambda batch: [])
    elif problem == "extra":
        monkeypatch.setattr(engine, "score_batch", lambda batch: [replace(original_batch(batch)[0], right_id="z0")])
    elif problem == "duplicate":
        pairs, count = [candidate(), candidate()], 2
    elif problem == "endpoint":
        pairs = [candidate("a", "missing")]
    elif problem == "reverse":
        pairs = [candidate("b", "a")]
    elif problem == "count":
        count = 2
    with pytest.raises(ScoringCoverageError):
        engine.write_scores(pairs, path, expected_count=count)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.partial.parquet"))
    assert not list(tmp_path.glob("*.coverage.db"))


def test_score_stability_identity_and_config_freezing(tmp_path):
    records = full_snapshot()
    cfg = ScoringConfig(batch_size=1)
    with pytest.raises(Exception):
        cfg.batch_size = 100
    first = ScoringEngine(MemorySnapshot(records), cfg)
    second = ScoringEngine(MemorySnapshot(list(reversed(records))), cfg)
    for label, engine in (("one", first), ("two", second)):
        engine.write_scores([candidate()], tmp_path / f"{label}.parquet", expected_count=1)
    assert pq.read_table(tmp_path / "one.parquet").equals(pq.read_table(tmp_path / "two.parquet"))
    assert first.identity == second.identity
    changed = ScoringEngine(MemorySnapshot(records), replace(cfg, batch_size=2))
    assert changed.identity["model_hash"] == first.identity["model_hash"]
    assert changed.identity["config_hash"] != first.identity["config_hash"]
    assert changed.score_batch([candidate()])[0].rule_score == first.score_batch([candidate()])[0].rule_score


def test_snapshot_mutation_is_not_published(tmp_path):
    records = full_snapshot()
    engine = ScoringEngine(MemorySnapshot(records))
    records[-1]["normalized_fields"]["phone"] = "different"
    with pytest.raises(ScoringInputChangedError):
        engine.write_scores([candidate()], tmp_path / "scores.parquet", expected_count=1)
    assert not (tmp_path / "scores.parquet").exists()


def test_run_artifact_refuses_different_model_config_and_policy_is_pinned(tmp_path, monkeypatch):
    records = full_snapshot()
    first = ScoringEngine(MemorySnapshot(records))
    score = first.score_batch([candidate()])[0]
    expected = first.decide(score, first.snapshot.records)
    monkeypatch.setattr("masterone.mdm.pipeline.policy_engine.get_policies", lambda: {})
    assert first.decide(score, first.snapshot.records) == expected
    path = tmp_path / "scores.parquet"
    first.write_scores([candidate()], path, expected_count=1)
    original = path.read_bytes()
    changed = ScoringEngine(MemorySnapshot(records), ScoringConfig(batch_size=1))
    with pytest.raises(ScoringInputChangedError, match="identity changed"):
        changed.write_scores([candidate()], path, expected_count=1)
    assert path.read_bytes() == original


def test_empty_artifact_has_full_contract_and_model_identity(tmp_path):
    engine = ScoringEngine(MemorySnapshot([]))
    path = tmp_path / "scores.parquet"
    metrics = engine.write_scores([], path, expected_count=0)
    assert metrics["coverage"] == "complete" and metrics["snapshot_records"] == 0
    assert pq.read_table(path).schema.field("rule_score").type == pa.float64()
    assert len(metrics["model_hash"]) == 64
    assert json.loads(path.with_suffix(".score.json").read_text(encoding="utf-8")) == metrics


def test_missing_splink_model_is_explicit_blocker_even_for_empty_run():
    with pytest.raises(SplinkModelBlockedError, match="no automatic training"):
        ScoringEngine(MemorySnapshot([]), ScoringConfig(mode="splink"))


def toy_splink_model():
    """Fixed TEST-ONLY arithmetic fixture. Not trained, calibrated or production."""
    import splink.comparison_library as cl
    comparison = cl.ExactMatch("name").create_comparison_dict("duckdb")
    levels = [level for level in comparison["comparison_levels"] if not level.get("is_null_level")]
    for level, m, u in zip(levels, (0.9, 0.1), (0.1, 0.9), strict=True):
        level.update(m_probability=m, u_probability=u)
    return {"link_type": "dedupe_only", "unique_id_column_name": "record_id", "comparisons": [comparison],
            "probability_two_random_records_match": 0.01,
            "blocking_rules_to_generate_predictions": []}


def test_real_splink_predictions_exact_registered_batches_and_cleanup():
    with duckdb.connect() as con:
        con.execute("CREATE TABLE inputs(record_id VARCHAR,name VARCHAR)")
        con.execute("INSERT INTO inputs VALUES ('a','an'),('b','an'),('c','hoa')")
        predictor = SplinkPredictor(con, "inputs", toy_splink_model())
        first = predictor.predict([("a", "b")])
        second = predictor.predict([("a", "c"), ("b", "c")])
        assert set(first) == {("a", "b")} and set(second) == {("a", "c"), ("b", "c")}
        assert first[("a", "b")][0] == pytest.approx(9 / (99 + 9))
        assert second[("a", "c")][0] < first[("a", "b")][0]
        assert predictor.predict([("a", "b")]) == first
        assert con.execute("SHOW TABLES").fetchall() == [("inputs",)]


def test_splink_engine_full_corpus_tf_batches_and_frozen_settings(tmp_path, monkeypatch):
    # Bypass readiness ONLY inside this test to exercise the real adapter with
    # explicitly uncalibrated fixed arithmetic settings; never production mode.
    model = toy_splink_model()
    model["comparisons"][0]["comparison_levels"][1].update(tf_adjustment_column="name", tf_adjustment_weight=1.0)
    model_hash = sha256(json.dumps(model).encode()).hexdigest()
    monkeypatch.setattr("masterone.mdm.pipeline.scoring_engine.load_frozen_model",
                         lambda config: (model, {"fields": {"name": "name"}}, "test-only"))
    records = [record("a", name="an"), record("b", name="an"), record("c", name="hoa"),
               record("d", name="hoa"), record("z", name="hoa")]
    config = ScoringConfig(mode="splink", batch_size=1, model_version="test-only", model_sha256=model_hash)
    engine = ScoringEngine(MemorySnapshot(records), config)
    # The loader's object is external mutable input; freeze settings on load.
    model["probability_two_random_records_match"] = 0.5
    path = tmp_path / "scores.parquet"
    engine.write_scores([candidate("a", "b"), candidate("c", "d")], path, expected_count=2)
    scores = [r for batch in iter_score_batches(path, 1) for r in batch]
    # TF derives from all 5 snapshot records, not only 4 candidate endpoints.
    assert scores[0].match_probability == pytest.approx((0.9 / 0.4) / (99 + 0.9 / 0.4))
    assert scores[1].match_probability == pytest.approx((0.9 / 0.6) / (99 + 0.9 / 0.6))
    assert all(r.model_hash == model_hash for r in scores)
    assert all(r.rule_score != r.match_probability for r in scores)
    assert not hasattr(engine, "splink_con")


@pytest.mark.parametrize("change", ["uncalibrated", "hash", "version", "fields", "unfitted"])
def test_invalid_or_uncalibrated_splink_artifact_rejected(tmp_path, change):
    model = toy_splink_model()
    if change == "unfitted":
        del model["comparisons"][0]["comparison_levels"][-1]["m_probability"]
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model), encoding="utf-8")
    digest = sha256(path.read_bytes()).hexdigest()
    # Metadata fixture exercises validation; this test never activates it.
    manifest = {"trained": True, "calibrated": True, "production_approved": True,
        "model_sha256": digest, "model_version": "test-fixture", "normalization_version": "p119_normalized_v1",
        "splink_version": "5.0.0", "training_snapshot_sha256": "a" * 64,
        "evaluation_report_sha256": "b" * 64, "fields": {"name": "name"}}
    if change == "uncalibrated":
        manifest["calibrated"] = False
    if change == "version":
        manifest["splink_version"] = "4.0.0"
    if change == "fields":
        manifest["fields"] = {"name": "name_normalized"}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    config = ScoringConfig(mode="splink", model_path=str(path), manifest_path=str(manifest_path),
                           model_version="test-fixture", model_sha256="bad" if change == "hash" else digest)
    with pytest.raises(SplinkModelBlockedError):
        load_frozen_model(config)
