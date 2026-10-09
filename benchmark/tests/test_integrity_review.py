"""Regression checks for G0/G1 isolation and benchmark integrity."""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from standalone.contracts.core import (
    DatasetMetadata, Decision, GoldAnnotations, GoldCoverage, GoldCoverageKind, LabeledPair,
    Pair, PairLabel, PairPrediction, Probability, Record, RecordFeatures, TaskType,
    canonical_pair_id, global_record_id,
)
from standalone.contracts.interfaces import FieldComparison
from standalone.datasets.audit import audit_masterone
from standalone.datasets.masterone import MasterOneAdapter
from standalone.datasets.splits import check_split_integrity
from standalone.datasets.universe import LegalPairUniverse
from standalone.evaluation.protocol import EvaluationStatus, pair_universe_coverage
from standalone.references.local import (
    AdministrativeUnit, LocalReferenceProvider, ReferenceBundle, load_local_references,
    validate_reference_bundle,
)
from standalone.runtime.paths import PathSafetyError, benchmark_root, cache_path, output_path


def _csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path, *, ids: tuple[str, str] = ("a", "b"),
             entity: str = "E1", case_type: str = "normal", split: str = "test",
             metadata_variant: bool = False) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "fixture"
    data.mkdir()
    record_columns = ["id", "entity_id", "source_system", "full_name", "phone_number",
                      "email_address", "raw_address", "address_id", "noise_type",
                      "difficulty", "temporal_case"]
    record_rows = [
        {"id": ids[0], "entity_id": entity, "source_system": "CRM", "full_name": "Nguyen An",
         "phone_number": "0912345678", "email_address": "an@example.test", "raw_address": "1 Pho Hue",
         "address_id": "ADDR-1", "noise_type": "clean", "difficulty": "easy", "temporal_case": "old"},
        {"id": ids[1], "entity_id": entity, "source_system": "ERP", "full_name": "Nguyen An",
         "phone_number": "0912345678", "email_address": "an@example.test", "raw_address": "1 Pho Hue",
         "address_id": "ADDR-2", "noise_type": "typo", "difficulty": "hard", "temporal_case": "new"},
    ]
    if metadata_variant:
        for row in record_rows:
            row.update({"address_id": "OTHER", "noise_type": "changed", "difficulty": "changed",
                        "temporal_case": "changed"})
    pair_columns = ["id_a", "id_b", "is_match", "case_type", "difficulty", "split",
                    "hard_negative_type", "entity_a", "entity_b"]
    pair_rows = [{"id_a": ids[0], "id_b": ids[1], "is_match": "1", "case_type": case_type,
                  "difficulty": "hard", "split": split, "hard_negative_type": "",
                  "entity_a": entity, "entity_b": entity}]
    if metadata_variant:
        pair_rows[0].update({"difficulty": "changed", "hard_negative_type": "changed"})
    _csv(data / "records.csv", record_columns, record_rows)
    _csv(data / "pairs.csv", pair_columns, pair_rows)
    return data


def test_output_and_cache_helpers_stay_in_their_own_directories() -> None:
    root = benchmark_root()
    with pytest.raises(PathSafetyError):
        output_path(str(root / "docs" / "escape.json"))
    with pytest.raises(PathSafetyError):
        cache_path(str(root / "results" / "escape.json"))
    assert output_path("review.json").parent == root / "results"
    assert cache_path("review.json").parent == root / ".cache"


def test_audit_artifact_writers_reject_outside_paths() -> None:
    from scripts.audit_g1_data import _dump, _write_audit_doc

    outside = benchmark_root().parent / "review-no-write-artifact.json"
    assert not outside.exists()
    with pytest.raises(PathSafetyError):
        _dump(outside, {"test": True})
    with pytest.raises(PathSafetyError):
        _write_audit_doc(outside, "test")
    assert not outside.exists()
    with pytest.raises(PathSafetyError):
        _dump(benchmark_root() / "docs" / "review-no-write.json", {"test": True})
    with pytest.raises(PathSafetyError):
        _write_audit_doc(benchmark_root() / "datasets" / "manifests" / "review-no-write.md", "test")


def test_audit_writer_rejects_redirected_manifest_directory(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import scripts.audit_g1_data as audit_script
    import standalone.runtime.paths as runtime_paths

    fake_root = tmp_path / "fake-root"
    redirected = tmp_path / "redirected"
    (fake_root / "datasets").mkdir(parents=True)
    redirected.mkdir()
    link = fake_root / "datasets" / "manifests"
    junction = False
    try:
        link.symlink_to(redirected, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "New-Item -ItemType Junction -Path $env:REVIEW_LINK_PATH -Target $env:REVIEW_TARGET_PATH | Out-Null"],
            env={**os.environ, "REVIEW_LINK_PATH": str(link), "REVIEW_TARGET_PATH": str(redirected)},
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
        junction = True
    monkeypatch.setattr(audit_script, "BENCHMARK_ROOT", fake_root)
    monkeypatch.setattr(runtime_paths, "benchmark_root", lambda: fake_root)
    try:
        with pytest.raises(PathSafetyError):
            audit_script._dump(link / "escape.json", {"test": True})
        assert not (redirected / "escape.json").exists()
    finally:
        if junction:
            os.rmdir(link)
        else:
            link.unlink()


def test_record_and_pair_identity_encodings_have_no_delimiter_collisions() -> None:
    assert global_record_id("a::b", "c", "d") != global_record_id("a", "b::c", "d")
    assert canonical_pair_id("a\0b", "c") != canonical_pair_id("a", "b\0c")
    assert canonical_pair_id("left", "right") == canonical_pair_id("right", "left")


def test_complete_pair_coverage_requires_actual_key_equality() -> None:
    coverage = GoldCoverage(pair_labels=GoldCoverageKind.COMPLETE_PAIR_LABELS)
    p1 = LabeledPair(Pair.create("a", "b"), PairLabel.POSITIVE, "test")
    p2 = LabeledPair(Pair.create("a", "c"), PairLabel.NEGATIVE, "test")
    assert pair_universe_coverage(coverage, 2, 2).status is EvaluationStatus.BLOCKED
    assert pair_universe_coverage(
        coverage, 2, 2, labeled_pairs=(p1, p1), legal_pair_ids=(p1.pair.pair_id, p2.pair.pair_id)
    ).status is EvaluationStatus.BLOCKED
    assert pair_universe_coverage(
        coverage, 2, 2, labeled_pairs=(p1, p2), legal_pair_ids=(p1.pair.pair_id, p2.pair.pair_id)
    ).status is EvaluationStatus.PASS
    assert pair_universe_coverage(
        coverage, 2, 2, labeled_pairs=(p1, replace(p2, label=PairLabel.UNKNOWN)),
        legal_pair_ids=(p1.pair.pair_id, p2.pair.pair_id)
    ).status is EvaluationStatus.BLOCKED


def test_comparison_and_feature_contracts_reject_nested_gold() -> None:
    with pytest.raises(ValueError):
        FieldComparison({"entity_id": "equal"})
    with pytest.raises(ValueError):
        RecordFeatures({"name": {"entity_id": "E1"}})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        Record("r", "CRM", {"name": {"is_match": "1"}})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        PairPrediction("pair:x", "0.7", Decision.REVIEW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GoldCoverage(pair_labels=GoldCoverageKind.COMPLETE_ENTITY_LABELS)
    assert PairPrediction("pair:x", Probability(0.7), Decision.REVIEW).score.kind == "probability"


def test_all_standalone_modules_import_without_application_or_optional_packages() -> None:
    root = benchmark_root()
    code = (
        "import builtins,importlib,pkgutil,sys;"
        "original=builtins.__import__;"
        "blocked={'src','app','splink','torch','transformers','pyspark','dedupe','lightgbm'};"
        "builtins.__import__=lambda name,*a,**k: (_ for _ in ()).throw(AssertionError('forbidden import: '+name)) "
        "if name.split('.')[0] in blocked else original(name,*a,**k);"
        "import standalone;"
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(standalone.__path__, 'standalone.')];"
        "assert not any(x.split('.')[0] in blocked for x in sys.modules)"
    )
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1", "TMP": str(root / ".cache"),
           "TEMP": str(root / ".cache"), "TMPDIR": str(root / ".cache")}
    result = subprocess.run([sys.executable, "-c", code], cwd=root.parent,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_adapter_requires_frozen_manifest_by_default(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    with pytest.raises(PathSafetyError):
        MasterOneAdapter(benchmark_root().parent / "outside-dataset")
    with pytest.raises(ValueError, match="freeze manifest"):
        MasterOneAdapter(data).load()
    assert len(MasterOneAdapter(data, require_manifest=False).load().pairs) == 1


def test_prediction_boundary_ignores_gold_mutation_and_id_bijection(tmp_path: Path) -> None:
    from standalone.contracts.interfaces import build_prediction_inputs

    first = MasterOneAdapter(_fixture(tmp_path / "first", ids=("a", "b")), require_manifest=False).load()
    second = MasterOneAdapter(_fixture(tmp_path / "second", ids=("x", "y"), entity="E-CHANGED",
                                       case_type="changed", metadata_variant=True),
                              require_manifest=False).load()
    first_input, first_trace = build_prediction_inputs(first)
    second_input, second_trace = build_prediction_inputs(second)
    assert first_input == second_input
    assert first_trace.record_ids != second_trace.record_ids
    assert first_trace.pair_ids != second_trace.pair_ids
    assert all(set(row.features.fields) == {"name", "phone", "email", "address"}
               for row in first_input.records)
    assert not hasattr(first_input.records[0], "record_id")
    assert not hasattr(first_input.pairs[0], "label")
    assert not hasattr(first_input.pairs[0], "pair_id")
    assert first.pairs[0].label is second.pairs[0].label is PairLabel.POSITIVE
    assert first_trace.pair_ids[0] == first.pairs[0].pair.pair_id
    assert second_trace.pair_ids[0] == second.pairs[0].pair.pair_id
    bijection = dict(zip(first_trace.record_ids, second_trace.record_ids))
    original_pair = first.pairs[0].pair
    assert canonical_pair_id(bijection[original_pair.left_record_id],
                             bijection[original_pair.right_record_id]) == second_trace.pair_ids[0]
    mutated = replace(first, pairs=(replace(first.pairs[0], label=PairLabel.NEGATIVE, split="dev",
                                             annotations={"case_type": "arbitrary", "split": "dev"}),),
                      gold=GoldAnnotations({}, {}))
    mutated_input, _ = build_prediction_inputs(mutated)
    assert mutated_input == first_input


def test_test_dataset_rejects_split_relabeling(tmp_path: Path) -> None:
    data = _fixture(tmp_path, split="train")
    with pytest.raises(ValueError, match="split"):
        MasterOneAdapter(data, require_manifest=False).load()


def test_split_checker_catches_negative_pair_endpoint_leakage() -> None:
    pair = LabeledPair(Pair.create("a", "b"), PairLabel.NEGATIVE, "train")
    report = check_split_integrity({"a": "train", "b": "test"}, {"a": "E1", "b": "E2"}, [pair])
    assert not report.valid
    assert pair.pair.pair_id in report.cross_partition_pairs


def test_legal_universe_rejects_count_and_metadata_mismatch() -> None:
    records = (Record("a", "A", {"name": "One"}), Record("b", "B", {"name": "Two"}))
    with pytest.raises(ValueError, match="source counts"):
        list(LegalPairUniverse(TaskType.CLEAN_CLEAN, {"A": 2, "B": 1}).iter_pairs(records))
    metadata = DatasetMetadata("fixture", "v1", TaskType.MULTI_SOURCE, ("A",),
                               frozenset({("A", "A")}))
    with pytest.raises(ValueError, match="absent from dataset metadata"):
        LegalPairUniverse.from_records(records, metadata)
    with pytest.raises(ValueError, match="two source names"):
        DatasetMetadata("fixture", "v1", TaskType.MULTI_SOURCE, ("A", "B"),
                        frozenset({"AB"}))  # type: ignore[arg-type]


def test_audit_marks_empty_record_join_key_invalid(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    with (data / "records.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["id"] = ""
    _csv(data / "records.csv", list(rows[0]), rows)
    _csv(data / "pairs.csv", ["id_a", "id_b", "is_match", "case_type", "difficulty", "split",
                              "hard_negative_type", "entity_a", "entity_b"], [])
    report = audit_masterone(data / "records.csv", data / "pairs.csv")
    assert not report.structurally_valid


def test_reference_candidates_preserve_same_id_across_target_versions() -> None:
    units = (
        AdministrativeUnit("old", "Old", "ward", None, "OLD", None),
        AdministrativeUnit("same", "New A", "ward", None, "NEW_A", None),
        AdministrativeUnit("same", "New B", "ward", None, "NEW_B", None),
    )
    mappings = (
        {"source_unit_id": "old", "source_admin_version": "OLD", "target_admin_version": "NEW_A",
         "mapping_status": "resolved", "target_units": [{"unit_id": "same", "unit_name": "New A"}]},
        {"source_unit_id": "old", "source_admin_version": "OLD", "target_admin_version": "NEW_B",
         "mapping_status": "resolved", "target_units": [{"unit_id": "same", "unit_name": "New B"}]},
    )
    bundle = ReferenceBundle("fixture", (), (), units, mappings, ())
    resolution = LocalReferenceProvider(bundle).map_old_to_new("old", "OLD")
    assert resolution.status == "ambiguous"
    assert {(c.admin_version, c.unit_id) for c in resolution.candidates} == {
        ("NEW_A", "same"), ("NEW_B", "same")
    }


def test_reference_manifest_detects_actual_bom_encoding() -> None:
    bundle, _ = load_local_references()
    by_name = {Path(item.relative_path).name: item for item in bundle.files}
    assert by_name["administrative_aliases_v1.csv"].encoding == "UTF-8 with BOM"
    assert by_name["mapping_entries_v1.json"].encoding == "UTF-8"


def test_reference_integrity_rejects_invalid_date_version_and_keeps_many_to_one() -> None:
    units = (
        AdministrativeUnit("old-a", "Old A", "ward", None, "OLD", None),
        AdministrativeUnit("old-b", "Old B", "ward", None, "OLD", None),
        AdministrativeUnit("new", "New", "ward", None, "NEW", date(2025, 7, 1)),
    )
    base = {"source_admin_version": "OLD", "target_admin_version": "NEW",
            "mapping_dataset_version": "fixture-v1", "mapping_status": "resolved",
            "direction": "old_to_new", "effective_date": "2025-07-01",
            "target_units": [{"unit_id": "new", "unit_name": "New"}]}
    mappings = ({**base, "source_unit_id": "old-a"}, {**base, "source_unit_id": "old-b"})
    bundle = ReferenceBundle("fixture", (), (), units, mappings, ())
    validate_reference_bundle(bundle)
    provider = LocalReferenceProvider(bundle)
    assert provider.map_old_to_new("old-a").candidates == provider.map_old_to_new("old-b").candidates
    assert provider.map_old_to_new("old-a", as_of=date(2025, 6, 30)).status == "not_effective"
    assert provider.map_old_to_new("old-a", as_of=date(2025, 7, 1)).status == "resolved"
    assert not hasattr(provider, "map_new_to_old")
    with pytest.raises(ValueError, match="invalid_mapping_dates"):
        validate_reference_bundle(replace(bundle, mappings=({**mappings[0], "effective_date": "2025-99-99"},)))
    with pytest.raises(ValueError, match="date differs"):
        validate_reference_bundle(replace(bundle, mappings=({**mappings[0], "effective_date": "2025-07-02"},)))
    with pytest.raises(ValueError, match="mapping_target_units_missing|invalid target admin version"):
        validate_reference_bundle(replace(bundle, mappings=({**mappings[0], "target_admin_version": "UNKNOWN"},)))
