from __future__ import annotations

import csv
import hashlib
import inspect
from collections import Counter
from dataclasses import fields
from pathlib import Path

import pytest

from standalone.contracts import (
    DataSplit, DatasetMetadata, GoldCoverage, GoldCoverageKind, LabeledPair, Pair, PairLabel,
    Decision, LogLikelihoodRatio, PairPrediction, Probability, Record, Similarity, TaskType,
    canonical_pair_id, global_record_id, parse_pair_label,
)
from standalone.contracts.core import FEATURE_ALLOWLIST, RecordFeatures
from standalone.contracts.interfaces import PairScorer
from standalone.datasets.audit import audit_masterone
from standalone.datasets.masterone import MasterOneAdapter
from standalone.datasets.splits import check_split_integrity
from standalone.datasets.universe import LegalPairUniverse
from standalone.evaluation.protocol import (
    EvaluationStatus, cluster_gold_coverage, pair_universe_coverage,
    validate_fit_split, validate_tuning_split,
)
from standalone.references.local import audit_reference_bundle, load_local_references

BENCHMARK = Path(__file__).resolve().parents[1]
DATASET_DIR = BENCHMARK / "datasets" / "masterone_test"


def _write_fixture(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _minimal_data(tmp_path: Path, pair_rows: list[dict[str, str]]) -> Path:
    data = tmp_path / "fixture"
    data.mkdir()
    rec_headers = ["id", "entity_id", "source_system", "full_name", "phone_number", "email_address",
                   "raw_address", "address_id", "noise_type", "difficulty", "temporal_case"]
    recs = [
        {"id": "a", "entity_id": "E1", "source_system": "A", "full_name": "Alex", "phone_number": "",
         "email_address": "a@example.test", "raw_address": "1 Road", "address_id": "", "noise_type": "",
         "difficulty": "", "temporal_case": ""},
        {"id": "b", "entity_id": "E1", "source_system": "B", "full_name": "Alex", "phone_number": "",
         "email_address": "a@example.test", "raw_address": "1 Road", "address_id": "", "noise_type": "",
         "difficulty": "", "temporal_case": ""},
    ]
    _write_fixture(data / "records.csv", rec_headers, recs)
    pair_headers = ["id_a", "id_b", "is_match", "case_type", "difficulty", "split", "hard_negative_type", "entity_a", "entity_b"]
    _write_fixture(data / "pairs.csv", pair_headers, pair_rows)
    return data


def _pair_row(a: str, b: str, label: str = "1", split: str = "test") -> dict[str, str]:
    return {"id_a": a, "id_b": b, "is_match": label, "case_type": "", "difficulty": "",
            "split": split, "hard_negative_type": "", "entity_a": "", "entity_b": ""}


def test_pair_label_parser_separates_positive_negative_unknown() -> None:
    assert parse_pair_label("1") is PairLabel.POSITIVE
    assert parse_pair_label("0") is PairLabel.NEGATIVE
    assert parse_pair_label("") is PairLabel.UNKNOWN
    assert parse_pair_label("unknown") is PairLabel.UNKNOWN
    with pytest.raises(ValueError, match="invalid pair label"):
        parse_pair_label("2")


def test_pair_canonicalization_is_order_independent_and_keeps_orientation() -> None:
    forward = Pair.create("dataset::A::1", "dataset::B::2", "A", "B")
    reverse = Pair.create("dataset::B::2", "dataset::A::1", "B", "A")
    assert canonical_pair_id(forward.left_record_id, forward.right_record_id) == forward.pair_id
    assert forward.pair_id == reverse.pair_id
    assert forward.source_orientation == ("A", "B")
    assert reverse.source_orientation == ("B", "A")
    assert forward.input_order != reverse.input_order
    assert global_record_id("ds", "CRM", "raw") == "ds::CRM::raw"
    with pytest.raises(ValueError, match="self-pairs"):
        Pair.create("same", "same")


def test_adapter_preserves_labels_and_separates_annotations() -> None:
    bundle = MasterOneAdapter(DATASET_DIR).load()
    counts = Counter(pair.label for pair in bundle.pairs)
    assert counts == {PairLabel.POSITIVE: 960, PairLabel.NEGATIVE: 240}
    assert bundle.gold_coverage.pair_labels is GoldCoverageKind.SAMPLED_LABELED_PAIRS
    assert bundle.gold_coverage.entity_labels is None
    assert bundle.metadata.sources == ("BILL", "CRM", "ERP")
    record = bundle.records[0]
    assert set(record.fields) == FEATURE_ALLOWLIST
    assert set(record.feature_view().fields) == FEATURE_ALLOWLIST
    with (DATASET_DIR / "records.csv").open("r", encoding="utf-8", newline="") as handle:
        raw = next(csv.DictReader(handle))
    assert record.record_id == global_record_id("masterone_test", raw["source_system"], raw["id"])
    assert record.source == raw["source_system"]
    assert record.fields == {
        "name": raw["full_name"], "phone": raw["phone_number"] or None,
        "email": raw["email_address"] or None, "address": raw["raw_address"] or None,
    }
    assert record.record_id in bundle.gold.record_annotations
    assert record.record_id in bundle.gold.entity_by_record_id
    assert "entity_id" not in record.__dict__
    with pytest.raises(ValueError, match="non-feature fields"):
        Record("id", "source", {"name": "x", "entity_id": "gold"})


def test_pair_scorer_signature_cannot_receive_record_or_gold() -> None:
    parameters = inspect.signature(PairScorer.score).parameters
    assert list(parameters) == ["self", "comparison"]
    assert "Pair" not in str(inspect.signature(PairScorer.score))
    assert "LabeledPair" not in str(inspect.signature(PairScorer.score))
    assert {field.name for field in fields(PairPrediction)} == {
        "pair_id", "score", "decision", "evidence", "stage_seconds"
    }
    prediction = PairPrediction("pair:abc", Probability(0.7), Decision.REVIEW)
    assert not hasattr(prediction, "label") and not hasattr(prediction, "entity_id")
    assert Similarity(0.92).kind == "similarity"
    assert LogLikelihoodRatio(2.4).kind == "log_likelihood_ratio"
    assert Probability(0.92).kind == "probability"
    with pytest.raises(ValueError):
        Probability(1.1)


def test_metadata_mutation_and_id_permutation_do_not_change_feature_view() -> None:
    gold_annotations = {"entity_id": "E1", "noise_type": "typo", "split": "test"}
    record_a = Record("dataset::CRM::id-1", "CRM", {"name": "Nguyen An", "email": None})
    feature_before = record_a.feature_view()
    gold_annotations.update({"entity_id": "E2", "noise_type": "clean", "split": "train"})
    record_b = Record("completely-permuted-id", "CRM", {"name": "Nguyen An", "email": None})
    assert feature_before == record_a.feature_view() == record_b.feature_view()
    assert not hasattr(feature_before, "record_id")
    assert not hasattr(feature_before, "entity_id")


def test_legal_pair_universe_dedupe_clean_clean_and_multisource() -> None:
    records = [Record(global_record_id("d", source, str(i)), source, {"name": str(i)})
               for source, count in (("A", 2), ("B", 3), ("C", 1)) for i in range(count)]
    dedupe = LegalPairUniverse(TaskType.DEDUPE, {"A": 2, "B": 3, "C": 1})
    assert dedupe.pair_count == 15
    assert len(list(dedupe.iter_pairs(records))) == 15
    clean = LegalPairUniverse(TaskType.CLEAN_CLEAN, {"A": 2, "B": 3})
    assert clean.pair_count == 6
    assert len(list(clean.iter_pairs([r for r in records if r.source in {"A", "B"}]))) == 6
    multi = LegalPairUniverse(TaskType.MULTI_SOURCE, {"A": 2, "B": 3, "C": 1},
                              frozenset({("A", "B"), ("B", "C"), ("A", "A")}))
    assert multi.pair_count == 10  # 2*3 + 3*1 + C(2,2)
    assert len(list(multi.iter_pairs(records))) == 10
    assert not multi.allows("C", "A")


def test_audit_detects_reversed_duplicate_self_dangling_and_bad_label(tmp_path: Path) -> None:
    rows = [_pair_row("a", "b", "1"), _pair_row("b", "a", "0"),
            _pair_row("a", "a", "0"), _pair_row("a", "missing", "1"), _pair_row("a", "b", "2")]
    data = _minimal_data(tmp_path, rows)
    report = audit_masterone(data / "records.csv", data / "pairs.csv")
    assert len(report.duplicate_pairs) == 1
    assert len(report.reversed_duplicate_pairs) == 1
    assert len(report.conflicting_pair_labels) == 1
    assert len(report.self_pairs) == 1
    assert len(report.dangling_pair_rows) == 1
    assert len(report.invalid_label_rows) == 1
    assert not report.structurally_valid


def test_audit_detects_duplicate_record_ids(tmp_path: Path) -> None:
    data = _minimal_data(tmp_path, [_pair_row("a", "b")])
    with (data / "records.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[1]["id"] = rows[0]["id"]
    _write_fixture(data / "records.csv", list(rows[0]), rows)
    report = audit_masterone(data / "records.csv", data / "pairs.csv")
    assert report.duplicate_record_ids == ("a",)


def test_adapter_rejects_dangling_pair_ids(tmp_path: Path) -> None:
    data = _minimal_data(tmp_path, [_pair_row("a", "missing")])
    with pytest.raises(ValueError, match="unknown record id"):
        MasterOneAdapter(data, require_manifest=False).load()


def test_adapter_rejects_schema_drift(tmp_path: Path) -> None:
    data = _minimal_data(tmp_path, [_pair_row("a", "b")])
    with (data / "records.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["new_unreviewed_field"] = ""
    _write_fixture(data / "records.csv", list(rows[0]), rows)
    with pytest.raises(ValueError, match="locked schema v1"):
        MasterOneAdapter(data, require_manifest=False).load()


def test_split_integrity_reports_entity_record_and_endpoint_leakage() -> None:
    a = "d::A::a"
    b = "d::A::b"
    c = "d::B::c"
    p1 = LabeledPair(Pair.create(a, b), PairLabel.POSITIVE, "train")
    p2 = LabeledPair(Pair.create(b, c), PairLabel.POSITIVE, "test")
    report = check_split_integrity({a: "train", b: "train", c: "test"},
                                   {a: "E1", b: "E2", c: "E1"}, [p1, p2],
                                   required_partitions=("train", "dev", "test"))
    assert not report.valid
    assert report.entity_overlap["E1"] == ("test", "train")
    assert b in report.record_overlap
    assert p2.pair.pair_id in report.cross_partition_pairs
    assert report.missing_required_partitions == ("dev",)


def test_split_checker_accepts_entity_disjoint_partition() -> None:
    a, b = "d::A::a", "d::B::b"
    pair = LabeledPair(Pair.create(a, b), PairLabel.NEGATIVE, "train")
    report = check_split_integrity({a: "train", b: "train"}, {a: "E1", b: "E2"}, [pair], ("train",))
    assert report.valid


def test_split_checker_blocks_missing_endpoint_or_entity_gold() -> None:
    a, missing = "d::A::a", "d::B::missing"
    pair = LabeledPair(Pair.create(a, missing), PairLabel.UNKNOWN, "test")
    report = check_split_integrity({a: "test"}, {}, [pair], ("test",))
    assert not report.valid
    assert report.unassigned_pair_endpoints == (missing,)
    assert report.records_missing_entity_labels == (a,)


def test_real_masterone_audit_and_manifest_inputs() -> None:
    report = audit_masterone(DATASET_DIR / "records.csv", DATASET_DIR / "pairs.csv")
    assert report.records.row_count == 4213
    assert report.pairs.row_count == 1200
    assert report.source_distribution == {"BILL": 1407, "CRM": 1408, "ERP": 1398}
    assert report.pair_split_distribution == {"test": 1200}
    assert report.pair_label_distribution == {"negative": 240, "positive": 960}
    assert report.pair_case_distribution["HARD_NEGATIVE"] == 240
    assert sum(value for slice_counts in report.pair_source_distribution.values()
               for label, value in slice_counts.items() if label == "negative") == 240
    assert report.records.empty_cells_by_column["phone_number"] == 137
    assert report.records.empty_cells_by_column["email_address"] == 273
    assert report.feature_allowlist == ("address", "email", "name", "phone")
    assert not report.duplicate_record_ids and not report.duplicate_pairs
    assert not report.self_pairs and not report.dangling_pair_rows and not report.invalid_label_rows
    assert report.same_source_duplicate_entity_groups == 617
    for source_slice in ("BILL|CRM", "BILL|ERP", "CRM|ERP"):
        assert report.pair_source_distribution[source_slice]["positive"] > 0
        assert report.pair_source_distribution[source_slice]["negative"] > 0
    bundle = MasterOneAdapter(DATASET_DIR).load()
    assert bundle.metadata.split_names == ("test",)
    actual_split = check_split_integrity(
        {record.record_id: "test" for record in bundle.records}, bundle.gold.entity_by_record_id,
        bundle.pairs, required_partitions=("train", "dev", "test"),
    )
    assert not actual_split.valid
    assert actual_split.missing_required_partitions == ("dev", "train")
    for item, path in ((report.records, DATASET_DIR / "records.csv"),
                       (report.pairs, DATASET_DIR / "pairs.csv")):
        assert item.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_reference_loader_preserves_ambiguity_and_ignores_backup() -> None:
    bundle, provider = load_local_references()
    ref_audit = audit_reference_bundle(bundle)
    assert ref_audit["bak_files_loaded"] is False
    assert all(not file.relative_path.endswith(".bak") for file in bundle.files)
    assert len(bundle.files) == 4
    ambiguous = next(row for row in bundle.mappings if row.get("mapping_status") == "ambiguous")
    resolution = provider.map_old_to_new(str(ambiguous["source_unit_id"]))
    assert resolution.status == "ambiguous"
    assert len(resolution.candidates) > 1
    assert resolution.evidence
    alias_groups: dict[tuple[str, str], set[str]] = {}
    for row in bundle.administrative_aliases:
        alias_groups.setdefault((row["alias"].casefold(), row["admin_version"]), set()).add(row["unit_id"])
    collision_alias, collision_version = next(key for key, ids in alias_groups.items() if len(ids) > 1)
    alias_candidates = provider.resolve_alias(collision_alias, collision_version)
    assert len(alias_candidates) > 1
    assert all(candidate.admin_version == collision_version for candidate in alias_candidates)
    assert provider.map_old_to_new("unit-does-not-exist").status == "unmapped"
    assert not hasattr(provider, "map_new_to_old")
    assert provider.resolve_column_alias("ho_ten", "V1")
    assert provider.resolve_column_alias("not_a_column_alias") == ()


def test_evaluation_coverage_gates_keep_unknown_unlabeled_pairs_blocked() -> None:
    coverage = GoldCoverage(pair_labels=GoldCoverageKind.SAMPLED_LABELED_PAIRS,
                            entity_labels=None, entity_labels_verified=False,
                            provenance="sampled listed pairs")
    pair_gate = pair_universe_coverage(coverage, labeled_pair_count=1200, legal_pair_count=8872578)
    assert pair_gate.status is EvaluationStatus.BLOCKED
    assert "unlisted pairs remain unknown" in pair_gate.reason
    assert cluster_gold_coverage(coverage).status is EvaluationStatus.BLOCKED


def test_training_and_tuning_split_guards_reject_test() -> None:
    validate_fit_split(DataSplit.TRAIN)
    validate_tuning_split(DataSplit.DEV)
    with pytest.raises(ValueError, match="fit/training may use train only"):
        validate_fit_split(DataSplit.TEST)
    with pytest.raises(ValueError, match="tuning may use dev only"):
        validate_tuning_split(DataSplit.TEST)


def test_manifest_pair_universe_does_not_assume_source_uniqueness() -> None:
    bundle = MasterOneAdapter(DATASET_DIR).load()
    universe = LegalPairUniverse.from_records(bundle.records, bundle.metadata)
    assert bundle.metadata.source_uniqueness_verified is False
    assert universe.pair_count == 8872578
    assert universe.allows("CRM", "CRM")


def test_dataset_manifest_locks_current_config_and_raw_hashes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json
    import standalone.datasets.masterone as adapter_module

    manifest = json.loads((BENCHMARK / "datasets/manifests/masterone_test_v1.json").read_text(encoding="utf-8"))
    config = json.loads((BENCHMARK / "standalone/config/datasets/masterone_test.json").read_text(encoding="utf-8"))
    from standalone.runtime.metadata import config_hash
    assert manifest["config_hash"] == config_hash(config)
    assert all(entry["sha256"] == hashlib.sha256((BENCHMARK / entry["relative_path"]).read_bytes()).hexdigest()
               for entry in manifest["files"])

    altered_root = tmp_path / "different-root"
    config_path = altered_root / "standalone/config/datasets/masterone_test.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"dataset_id":"changed"}', encoding="utf-8")
    monkeypatch.setattr(adapter_module, "benchmark_root", lambda: altered_root)
    with pytest.raises(ValueError, match="config hash differs"):
        MasterOneAdapter(DATASET_DIR).load()
