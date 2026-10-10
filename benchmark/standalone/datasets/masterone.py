"""Independent adapter for the internal MasterOne test dataset."""
from __future__ import annotations

import csv
import hashlib
import json
from itertools import combinations_with_replacement
from pathlib import Path

from standalone.contracts import (
    DatasetBundle, DatasetMetadata, GoldAnnotations, GoldCoverage, GoldCoverageKind,
    LabeledPair, Pair, PairLabel, Record, TaskType, global_record_id, parse_pair_label,
)
from standalone.contracts.core import FEATURE_ALLOWLIST
from standalone.contracts.interfaces import DatasetAdapter
from standalone.runtime.metadata import config_hash
from standalone.runtime.paths import benchmark_root, resolve_benchmark_path

RECORD_REQUIRED = frozenset({"id", "entity_id", "source_system", "full_name", "phone_number", "email_address", "raw_address"})
PAIR_REQUIRED = frozenset({"id_a", "id_b", "is_match", "split"})
RECORD_SCHEMA_V1 = frozenset({
    "id", "entity_id", "source_system", "full_name", "phone_number", "email_address",
    "raw_address", "address_id", "noise_type", "difficulty", "temporal_case",
})
PAIR_SCHEMA_V1 = frozenset({
    "id_a", "id_b", "is_match", "case_type", "difficulty", "split",
    "hard_negative_type", "entity_a", "entity_b",
})
FIELD_MAPPING = {"full_name": "name", "phone_number": "phone", "email_address": "email", "raw_address": "address"}
RECORD_GOLD_FIELDS = frozenset({"entity_id", "address_id", "noise_type", "difficulty", "temporal_case"})
PAIR_GOLD_FIELDS = frozenset({"is_match", "case_type", "difficulty", "split", "hard_negative_type", "entity_a", "entity_b"})


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"CSV has duplicate column names: {path}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"CSV has malformed row width: {path}")
    return list(reader.fieldnames), rows


def _global_id(dataset_id: str, source: str, raw_id: str) -> str:
    return global_record_id(dataset_id, source, raw_id)


class MasterOneAdapter(DatasetAdapter):
    def __init__(self, dataset_dir: str | Path, dataset_id: str = "masterone_test",
                 version: str = "internal-test-v1", *, require_manifest: bool = True) -> None:
        self.dataset_dir = resolve_benchmark_path(dataset_dir)
        self.dataset_id = dataset_id
        self.version = version
        self.require_manifest = require_manifest

    def _verify_frozen_manifest(self, records_path: Path, pairs_path: Path,
                                record_schema: list[str], pair_schema: list[str]) -> None:
        manifest_path = resolve_benchmark_path(
            self.dataset_dir.parent / "manifests" / f"{self.dataset_id}_v1.json")
        if not manifest_path.is_file():
            if self.require_manifest:
                raise ValueError(f"required freeze manifest is missing: {manifest_path}")
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read dataset freeze manifest {manifest_path}: {exc}") from exc
        if manifest.get("dataset_id") != self.dataset_id or manifest.get("dataset_version") != self.version:
            raise ValueError("dataset ID/version does not match its freeze manifest")
        config_path = resolve_benchmark_path(
            benchmark_root() / "standalone" / "config" / "datasets" / f"{self.dataset_id}.json")
        try:
            expected_config_hash = config_hash(json.loads(config_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read dataset config {config_path}: {exc}") from exc
        frozen_config_hash = manifest.get("config_hash")
        if frozen_config_hash is None:
            raise ValueError("freeze manifest has no config_hash; regenerate its manifest before loading")
        if frozen_config_hash != expected_config_hash:
            raise ValueError("dataset config hash differs from its freeze manifest; create a new dataset version")
        raw_entries = manifest.get("files")
        if not isinstance(raw_entries, list) or any(not isinstance(row, dict) for row in raw_entries):
            raise ValueError("freeze manifest files must be a list of objects")
        entries = {Path(row.get("relative_path", "")).name: row for row in raw_entries}
        if len(entries) != len(raw_entries):
            raise ValueError("freeze manifest has duplicate file entries")
        for name, path, schema in (("records.csv", records_path, record_schema), ("pairs.csv", pairs_path, pair_schema)):
            entry = entries.get(name)
            if entry is None:
                raise ValueError(f"freeze manifest has no entry for {name}")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_hash != entry.get("sha256"):
                raise ValueError(f"frozen source hash changed for {name}; create a new dataset version")
            if schema != entry.get("schema"):
                raise ValueError(f"source schema changed for {name}; create a new dataset version")

    def load(self) -> DatasetBundle:
        records_path = resolve_benchmark_path(self.dataset_dir / "records.csv")
        pairs_path = resolve_benchmark_path(self.dataset_dir / "pairs.csv")
        record_schema, raw_records = _read_csv(records_path)
        pair_schema, raw_pairs = _read_csv(pairs_path)
        missing_record_columns = RECORD_REQUIRED - set(record_schema)
        missing_pair_columns = PAIR_REQUIRED - set(pair_schema)
        if missing_record_columns:
            raise ValueError(f"records.csv missing required columns: {sorted(missing_record_columns)}")
        if missing_pair_columns:
            raise ValueError(f"pairs.csv missing required columns: {sorted(missing_pair_columns)}")
        if set(record_schema) != RECORD_SCHEMA_V1:
            raise ValueError(f"records.csv schema differs from locked schema v1; unexpected={sorted(set(record_schema)-RECORD_SCHEMA_V1)}, missing={sorted(RECORD_SCHEMA_V1-set(record_schema))}")
        if set(pair_schema) != PAIR_SCHEMA_V1:
            raise ValueError(f"pairs.csv schema differs from locked schema v1; unexpected={sorted(set(pair_schema)-PAIR_SCHEMA_V1)}, missing={sorted(PAIR_SCHEMA_V1-set(pair_schema))}")
        self._verify_frozen_manifest(records_path, pairs_path, record_schema, pair_schema)

        records: list[Record] = []
        by_raw_id: dict[str, Record] = {}
        entity_by_id: dict[str, str] = {}
        record_annotations: dict[str, dict[str, str]] = {}
        for row_number, row in enumerate(raw_records, 2):
            raw_id = row["id"].strip()
            source = row["source_system"].strip()
            if not raw_id or not source:
                raise ValueError(f"records.csv row {row_number}: id and source_system are required")
            if raw_id in by_raw_id:
                raise ValueError(f"records.csv has duplicate raw id {raw_id!r}")
            record_id = _global_id(self.dataset_id, source, raw_id)
            fields = {target: (row.get(source_name) or None) for source_name, target in FIELD_MAPPING.items()}
            if set(fields) - FEATURE_ALLOWLIST:
                raise AssertionError("adapter field map escaped the feature allowlist")
            record = Record(record_id, source, fields)
            records.append(record)
            by_raw_id[raw_id] = record
            if row.get("entity_id", "").strip():
                entity_by_id[record_id] = row["entity_id"].strip()
            record_annotations[record_id] = {
                key: value for key, value in row.items()
                if key not in {"id", *FIELD_MAPPING.keys(), "source_system"}
            }

        labeled_pairs: list[LabeledPair] = []
        seen: dict[str, PairLabel] = {}
        splits: set[str] = set()
        for row_number, row in enumerate(raw_pairs, 2):
            raw_a, raw_b = row["id_a"].strip(), row["id_b"].strip()
            if raw_a not in by_raw_id or raw_b not in by_raw_id:
                dangling = raw_a if raw_a not in by_raw_id else raw_b
                raise ValueError(f"pairs.csv row {row_number} references unknown record id {dangling!r}")
            record_a, record_b = by_raw_id[raw_a], by_raw_id[raw_b]
            pair = Pair.create(record_a.record_id, record_b.record_id, record_a.source, record_b.source)
            label = parse_pair_label(row["is_match"])
            if pair.pair_id in seen:
                prior = seen[pair.pair_id]
                relation = "conflicting labels" if prior is not label else "duplicate/reversed duplicate"
                raise ValueError(f"pairs.csv contains {relation} for pair {pair.pair_id}")
            seen[pair.pair_id] = label
            split = (row.get("split") or "").strip()
            if self.version == "internal-test-v1" and split != "test":
                raise ValueError(f"pairs.csv row {row_number}: split {split!r} violates frozen test-only protocol")
            if not split:
                raise ValueError(f"pairs.csv row {row_number}: split is required")
            splits.add(split)
            annotations = {
                key: value for key, value in row.items()
                if key not in {"id_a", "id_b", "is_match"}
            }
            labeled_pairs.append(LabeledPair(pair, label, split, annotations))

        sources = tuple(sorted({record.source for record in records}))
        # No evidence says each source is internally deduplicated. Keep same-source
        # pairs in the legal universe as well as cross-source pairs.
        source_pairs = frozenset(combinations_with_replacement(sources, 2))
        metadata = DatasetMetadata(
            dataset_id=self.dataset_id, version=self.version, task=TaskType.MULTI_SOURCE,
            sources=sources, allowed_source_pairs=source_pairs,
            provenance="team-generated internal data; label/entity semantics not independently adjudicated",
            split_names=tuple(sorted(splits)), source_uniqueness_verified=False,
        )
        coverage = GoldCoverage(
            pair_labels=GoldCoverageKind.SAMPLED_LABELED_PAIRS if labeled_pairs else None,
            entity_labels=None,
            entity_labels_verified=False,
            provenance="pairs.csv labels are sampled test pairs; entity_id is held separately and unverified",
        )
        gold = GoldAnnotations(entity_by_id, record_annotations)
        return DatasetBundle(tuple(records), tuple(labeled_pairs), metadata, coverage, gold)
