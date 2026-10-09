"""Coverage, canonical provenance and execution-mode equivalence contracts."""

import importlib.util
import json
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from masterone.mdm.pipeline.blocking import generate_candidates
from masterone.mdm.pipeline.candidate_engine import (
    CandidateConfig,
    CandidateEngine,
    CandidateWorkloadExceededError,
    CompositeKey,
    CompositeRule,
)
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.pipeline.standardization import standardize
from masterone.mdm.resolution_service import _compute


def record(record_id, **fields):
    return {"id": record_id, "external_id": record_id, "source_system": "CRM",
            "entity_type": "PERSON", "raw_fields": {}, "quality_flags": [],
            "normalized_fields": fields}


def snapshot():
    fields = {"name": "nguyen van an", "phone": "0901234567",
              "phones": [{"value": "0901234567", "quality": "VALID"}],
              "emails": [{"value": "a@example.com", "quality": "VALID"}],
              "government_id": "012345678901", "_field_quality": {"phone": "VALID", "government_id": "VALID"},
              "dob": "1990-01-02", "address": "so 12 duong mot",
              "address_parts": {"province": "ha noi"}, "_name_parts": {"surname": "nguyen"}}
    return [record("z", **fields), record("a", **fields),
            record("other", name="tran thi hoa", address="so 12 duong hai", dob="1985-01-01")]


def legacy_candidates(records, **kwargs):
    path = Path(__file__).parents[1] / "fixtures" / "legacy_candidate_reference.py"
    spec = importlib.util.spec_from_file_location("legacy_candidates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_candidates(records, **kwargs)


def test_cross_chunk_phone_global_identity():
    engine = CandidateEngine(CandidateConfig(include_tfidf=False))
    engine.add_records([record("z", phones=[{"value": "0901234567", "quality": "VALID"}])])
    engine.add_records([record("a", phones=[{"value": "0901234567", "quality": "VALID"}])])
    assert engine.finalize().pairs == {("a", "z"): {"exact_phone"}}


def test_union_preserves_all_sources_and_deduplicates(monkeypatch):
    monkeypatch.setattr("masterone.mdm.pipeline.candidate_engine.tfidf_candidates",
                        lambda records: {("z", "a"): {"tfidf_name", "another_tfidf_pass"},
                                         ("a", "z"): {"tfidf_name"}, ("a", "a"): {"tfidf_name"}})
    result = CandidateEngine().generate(snapshot()[:2])
    assert result.pairs == {("a", "z"): {"exact_phone", "exact_email", "exact_gov_id", "exact_name",
                                             "name_gram", "address_gram", "name_prefix_year", "surname_year",
                                             "name_province", "phone_suffix_year", "tfidf_name", "another_tfidf_pass"}}
    assert result.metrics["candidate_count"] == 1
    assert result.metrics["union_collapsed_observations"] > 0


@pytest.mark.parametrize("quality", ["SHARED", "INVALID", "PLACEHOLDER", "MISSING"])
def test_untrusted_exact_values_are_excluded(quality):
    fields = {"phones": [{"value": "0901234567", "quality": quality}],
              "emails": [{"value": "a@example.com", "quality": quality}],
              "government_id": "012345678901", "_field_quality": {"government_id": quality}}
    result = CandidateEngine().generate([record("a", **fields), record("z", **fields)])
    assert result.pairs == {}
    assert result.metrics["excluded_records_by_source"]["exact_phone"] == 2


def test_secondary_valid_contacts_and_exact_name_without_quality():
    fields = {"name": "abc", "phones": [{"value": "bad", "quality": "INVALID"},
                                        {"value": "0901234567", "quality": "VALID"}]}
    result = CandidateEngine().generate([record("a", **fields), record("z", **fields)])
    assert {"exact_phone", "exact_name"} <= result.pairs[("a", "z")]


def test_configurable_composite():
    rule = CompositeRule("custom_name_year", (CompositeKey("name", "prefix", 5), CompositeKey("dob", "year")))
    config = CandidateConfig(composites=(rule,), include_grams=False, include_tfidf=False)
    result = CandidateEngine(config).generate([record("a", name="abcde one", dob="1990-01-01"),
                                               record("z", name="abcde two", dob="1990-02-02")])
    assert result.pairs == {("a", "z"): {"custom_name_year"}}


@pytest.mark.parametrize("limit,reason", [(2, "max_block_size"), (3, "max_pairs_per_block")])
def test_block_guards_report_exact_skipped_pair_count(limit, reason):
    config = CandidateConfig(contact_block_size=limit, max_pairs_per_block=2,
                             include_grams=False, include_tfidf=False)
    records = [record(str(i), phones=[{"value": "0901234567", "quality": "VALID"}]) for i in range(3)]
    result = CandidateEngine(config).generate(records)
    assert result.pairs == {}
    assert result.metrics["skipped_pair_observations"] == 3
    assert result.metrics["skip_reasons"] == {reason: 3}
    assert result.block_log[0]["reason"] == reason
    assert "0901234567" not in json.dumps(result.block_log)


def test_global_pair_budget_fails_without_returning_truncated_candidates():
    records = [record(str(i), name="abc") for i in range(3)]
    with pytest.raises(CandidateWorkloadExceededError) as error:
        CandidateEngine(CandidateConfig(max_unique_pairs=2, include_tfidf=False)).generate(records)
    assert error.value.metrics["failure_reason"] == "global_pair_limit_exceeded"


def test_duplicate_snapshot_ids_fail_across_chunks():
    engine = CandidateEngine()
    engine.add_records([record("a")])
    with pytest.raises(ValueError, match="Duplicate snapshot"):
        engine.add_records([record("a")])


def test_ingested_snapshot_is_not_changed_by_caller_mutation():
    records = snapshot()
    engine = CandidateEngine()
    engine.add_records(records)
    expected = engine.finalize()
    records[0]["normalized_fields"]["name"] = "changed after ingestion"
    assert engine.finalize() == expected


def test_missing_tfidf_dependency_does_not_silently_reduce_coverage(monkeypatch):
    import builtins

    original = builtins.__import__

    def without_sklearn(name, *args, **kwargs):
        if name.startswith("sklearn"):
            raise ImportError("sklearn unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sklearn)
    with pytest.raises(ImportError, match="sklearn unavailable"):
        CandidateEngine().generate(snapshot())


def test_deterministic_with_tfidf_ties_and_chunk_permutations():
    records = [record(f"r{i:02d}", name="nguyen van an") for i in range(25)]
    expected = CandidateEngine().generate(records)
    for seed in range(4):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        engine = CandidateEngine()
        for offset in range(0, len(shuffled), 3):
            engine.add_records(shuffled[offset:offset + 3])
        actual = engine.finalize()
        assert actual == expected
        assert list(actual.pairs) == sorted(actual.pairs)


@pytest.mark.parametrize("size", [3, 15, 20, 21, 30, 40, 60, 61])
def test_default_coverage_equals_frozen_legacy_at_guard_boundaries(size):
    records = [record(f"r{i:03d}", **snapshot()[0]["normalized_fields"]) for i in range(size)]
    expected = legacy_candidates(records)
    assert CandidateEngine().generate(records).pairs == expected
    assert generate_candidates(records) == expected


def test_mixed_snapshot_coverage_and_legacy_name_limit():
    records = sorted(snapshot(), key=lambda r: r["id"])
    assert CandidateEngine().generate(records).pairs == legacy_candidates(records)
    assert generate_candidates(records, max_bucket_size=1) == legacy_candidates(records, max_bucket_size=1)


@pytest.mark.parametrize("batch_size", [1, 2, 50000])
def test_forced_small_large_on_same_snapshot_and_artifact_sources(tmp_path, batch_size):
    records = snapshot()
    source = tmp_path / "normalized.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": r["id"], "normalized_fields": json.dumps(r["normalized_fields"])}
                                       for r in records]), source)
    runner = StageRunner("equivalence", "a1", output_dir=tmp_path)
    try:
        path = tmp_path / "candidates.parquet"
        large = runner.build_candidates(str(source), str(path), batch_size=batch_size)
        small = CandidateEngine().generate(records)
        assert large == small
        rows = pq.read_table(path).to_pylist()
        assert {(r["left_id"], r["right_id"]): set(r["reasons"]) for r in rows} == small.pairs
        assert all(r["reasons"] == sorted(r["reasons"]) for r in rows)
        assert json.loads(path.with_suffix(".metrics.json").read_text()) == small.metrics
    finally:
        runner.con.close()


def test_full_stage_path_keeps_imported_snapshot_and_matches_small(tmp_path):
    fields, flags = standardize({"name": "Nguyen Van An", "phone": "0901234567"}, "PERSON")
    records = [record("a", **fields), record("z", **fields)]
    for item in records:
        item["quality_flags"] = flags
        # Deliberately different raw data: candidates must use the snapshot.
        item["raw_fields"] = {"name": "different " + item["id"]}
    runner = StageRunner("equivalence", "a1", output_dir=tmp_path)
    try:
        large = runner.run_all(records)
        small = _compute(records, "equivalence", "person-v3")
        assert large["candidate_metrics"] == small["candidate_metrics"]
        assert [(p["id"], p["blocking_reasons"]) for p in large["pairs"]] == [
            (p["id"], p["blocking_reasons"]) for p in small["pairs"]]
    finally:
        runner.con.close()


def test_above_10000_records_cross_chunk_phone(tmp_path):
    records = [record(f"r{i:05d}") for i in range(10001)]
    records[0]["normalized_fields"] = {"phones": [{"value": "0901234567", "quality": "VALID"}]}
    records[-1]["normalized_fields"] = records[0]["normalized_fields"]
    source = tmp_path / "large.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": r["id"], "normalized_fields": json.dumps(r["normalized_fields"])}
                                       for r in records]), source)
    runner = StageRunner("large", "a1", output_dir=tmp_path)
    try:
        large = runner.build_candidates(str(source), str(tmp_path / "pairs.parquet"), batch_size=1000)
        assert large.pairs == generate_candidates(records) == {("r00000", "r10000"): {"exact_phone"}}
    finally:
        runner.con.close()


def test_large_artifact_keeps_all_tfidf_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("masterone.mdm.pipeline.candidate_engine.tfidf_candidates",
                        lambda records: {("z", "a"): {"tfidf_name", "tfidf_address"}})
    source = tmp_path / "snapshot.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": item["id"], "normalized_fields": json.dumps(item["normalized_fields"])}
                                       for item in snapshot()[:2]]), source)
    runner = StageRunner("sources", "a1", output_dir=tmp_path)
    try:
        path = tmp_path / "pairs.parquet"
        result = runner.build_candidates(str(source), str(path), batch_size=1)
        rows = pq.read_table(path).to_pylist()
        assert len(rows) == 1
        assert set(rows[0]["reasons"]) == result.pairs[("a", "z")]
        assert {"tfidf_name", "tfidf_address", "exact_phone", "name_gram"} <= set(rows[0]["reasons"])
        assert runner.con.execute("SELECT reason FROM cand_blocks ORDER BY reason").fetchall() == [
            (source,) for source in sorted(result.pairs[("a", "z")])]
    finally:
        runner.con.close()


@pytest.mark.parametrize("config", [lambda: CandidateConfig(max_block_size=0),
                                    lambda: CompositeKey("name", "unknown"),
                                    lambda: CandidateConfig(composites=(CompositeRule("exact_phone", (CompositeKey("name"),)),))])
def test_invalid_configuration_fails(config):
    with pytest.raises(ValueError):
        config()
