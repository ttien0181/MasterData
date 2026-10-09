"""Reproducible, label-aware audit of the frozen internal MasterOne source files."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations_with_replacement
from pathlib import Path
from typing import Any

from standalone.contracts import PairLabel, parse_pair_label
from standalone.contracts.core import FEATURE_ALLOWLIST
from standalone.datasets.masterone import FIELD_MAPPING
from standalone.runtime.metadata import config_hash

LEAKAGE_FIELDS = frozenset({
    "entity_id", "address_id", "is_match", "entity_a", "entity_b", "noise_type", "difficulty",
    "case_type", "hard_negative_type", "temporal_case", "split", "generator_label",
})


@dataclass(frozen=True)
class CsvAudit:
    relative_path: str
    sha256: str
    encoding: str
    row_count: int
    schema: tuple[str, ...]
    empty_cells_by_column: dict[str, int]


@dataclass(frozen=True)
class MasterOneAudit:
    dataset_version: str
    generated_at_utc: str
    records: CsvAudit
    pairs: CsvAudit
    duplicate_record_ids: tuple[str, ...]
    duplicate_pairs: tuple[str, ...]
    reversed_duplicate_pairs: tuple[str, ...]
    self_pairs: tuple[int, ...]
    dangling_pair_rows: tuple[int, ...]
    invalid_label_rows: tuple[tuple[int, str], ...]
    conflicting_pair_labels: tuple[str, ...]
    entity_pair_label_conflicts: tuple[str, ...]
    source_distribution: dict[str, int]
    entity_distribution: dict[str, int]
    entity_count_distribution: dict[str, int]
    same_source_duplicate_entity_groups: int
    pair_split_distribution: dict[str, int]
    pair_label_distribution: dict[str, int]
    pair_source_distribution: dict[str, dict[str, int]]
    pair_case_distribution: dict[str, int]
    hard_negative_type_distribution: dict[str, int]
    record_leakage_columns: tuple[str, ...]
    pair_leakage_columns: tuple[str, ...]
    feature_allowlist: tuple[str, ...]
    record_id_policy: str

    @property
    def structurally_valid(self) -> bool:
        critical_empty = any(self.records.empty_cells_by_column.get(name, 0)
                             for name in ("id", "source_system")) or any(
                                 self.pairs.empty_cells_by_column.get(name, 0)
                                 for name in ("id_a", "id_b", "split"))
        return not any((self.duplicate_record_ids, self.self_pairs, self.dangling_pair_rows,
                        self.invalid_label_rows, self.duplicate_pairs, self.conflicting_pair_labels,
                        self.entity_pair_label_conflicts, critical_empty))

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["structurally_valid"] = self.structurally_valid
        return result


def _read(path: Path) -> tuple[list[str], list[dict[str, str]], bytes, str]:
    raw = path.read_bytes()
    try:
        raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"input is not valid UTF-8: {path}") from exc
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"invalid or duplicate CSV header: {path}")
        schema = list(reader.fieldnames)
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"rows have malformed width relative to the header: {path}")
    return schema, rows, raw, "UTF-8" if not raw.startswith(b"\xef\xbb\xbf") else "UTF-8 with BOM"


def _csv_audit(path: Path, relative_path: str, schema: list[str], rows: list[dict[str, str]], raw: bytes, encoding: str) -> CsvAudit:
    empty = {key: sum(1 for row in rows if not (row.get(key) or "").strip()) for key in schema}
    return CsvAudit(relative_path, hashlib.sha256(raw).hexdigest(), encoding, len(rows),
                    tuple(schema), empty)


def audit_masterone(records_path: str | Path, pairs_path: str | Path,
                    dataset_version: str = "internal-test-v1") -> MasterOneAudit:
    records_path, pairs_path = Path(records_path), Path(pairs_path)
    record_schema, record_rows, record_bytes, record_encoding = _read(records_path)
    pair_schema, pair_rows, pair_bytes, pair_encoding = _read(pairs_path)
    required_record = {"id", "entity_id", "source_system", *FIELD_MAPPING.keys()}
    required_pair = {"id_a", "id_b", "is_match", "split"}
    if missing := required_record - set(record_schema):
        raise ValueError(f"records.csv missing required columns: {sorted(missing)}")
    if missing := required_pair - set(pair_schema):
        raise ValueError(f"pairs.csv missing required columns: {sorted(missing)}")

    id_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_id: dict[str, dict[str, str]] = {}
    for row in record_rows:
        rid = (row.get("id") or "").strip()
        id_rows[rid].append(row)
        if rid and rid not in by_id:
            by_id[rid] = row
    duplicate_record_ids = tuple(sorted(rid for rid, rows in id_rows.items() if rid and len(rows) > 1))
    sources = Counter((row.get("source_system") or "").strip() or "<empty>" for row in record_rows)
    entities = Counter((row.get("entity_id") or "").strip() or "<empty>" for row in record_rows)
    entity_source_counts: Counter[tuple[str, str]] = Counter(
        ((row.get("entity_id") or "").strip(), (row.get("source_system") or "").strip())
        for row in record_rows if (row.get("entity_id") or "").strip()
    )
    entity_count_dist = Counter(str(count) for (_entity, _source), count in entity_source_counts.items())
    same_source_duplicate_groups = sum(count > 1 for count in entity_source_counts.values())
    pair_splits = Counter((row.get("split") or "").strip() or "<empty>" for row in pair_rows)
    pair_labels: Counter[str] = Counter()
    pair_groups: dict[tuple[str, str], list[tuple[int, str, str]]] = defaultdict(list)
    self_pairs: list[int] = []
    dangling: list[int] = []
    invalid_labels: list[tuple[int, str]] = []
    entity_conflicts: list[str] = []
    pair_source_dist: dict[str, Counter[str]] = defaultdict(Counter)
    pair_cases = Counter((row.get("case_type") or "").strip() or "<empty>" for row in pair_rows)
    hard_negative_types = Counter((row.get("hard_negative_type") or "").strip() or "<empty>" for row in pair_rows)
    for line_no, row in enumerate(pair_rows, 2):
        a, b = (row.get("id_a") or "").strip(), (row.get("id_b") or "").strip()
        if a == b:
            self_pairs.append(line_no)
        if a not in by_id or b not in by_id:
            dangling.append(line_no)
        key = tuple(sorted((a, b)))
        raw_label = (row.get("is_match") or "").strip()
        try:
            label = parse_pair_label(raw_label)
            pair_labels[label.value] += 1
        except ValueError:
            invalid_labels.append((line_no, raw_label))
            label = PairLabel.UNKNOWN
        pair_groups[key].append((line_no, raw_label, (row.get("split") or "").strip()))
        if a in by_id and b in by_id and label is not PairLabel.UNKNOWN:
            src_a = (by_id[a].get("source_system") or "").strip() or "<empty>"
            src_b = (by_id[b].get("source_system") or "").strip() or "<empty>"
            pair_source_dist["|".join(sorted((src_a, src_b)))][label.value] += 1
            entity_a, entity_b = by_id[a].get("entity_id", "").strip(), by_id[b].get("entity_id", "").strip()
            if entity_a and entity_b and ((label is PairLabel.POSITIVE) != (entity_a == entity_b)):
                entity_conflicts.append(f"{line_no}:{a}|{b}")

    duplicate_pairs: list[str] = []
    reversed_pairs: list[str] = []
    conflicting: list[str] = []
    for (a, b), group in pair_groups.items():
        if len(group) > 1:
            duplicate_pairs.append(f"{a}|{b}")
            # Detect a reversed duplicate from raw input orientation, independent of labels.
            raw_orientations = []
            for line_no, _, _ in group:
                row = pair_rows[line_no - 2]
                raw_orientations.append(((row.get("id_a") or "").strip(), (row.get("id_b") or "").strip()))
            if len(set(raw_orientations)) > 1:
                reversed_pairs.append(f"{a}|{b}")
            known_labels: set[PairLabel] = set()
            for _, raw_value, _ in group:
                try:
                    label = parse_pair_label(raw_value)
                    if label is not PairLabel.UNKNOWN:
                        known_labels.add(label)
                except ValueError:
                    pass
            if len(known_labels) > 1:
                conflicting.append(f"{a}|{b}")

    # Raw field names are mapped by the adapter; manifest the canonical features.
    actual_features = tuple(sorted(FIELD_MAPPING.values()))
    record_leaks = tuple(sorted(set(record_schema) & LEAKAGE_FIELDS))
    pair_leaks = tuple(sorted(set(pair_schema) & LEAKAGE_FIELDS))
    base = records_path.parent.parent.parent
    try:
        rec_rel = records_path.relative_to(base).as_posix()
        pair_rel = pairs_path.relative_to(base).as_posix()
    except ValueError:
        rec_rel, pair_rel = records_path.name, pairs_path.name
    return MasterOneAudit(
        dataset_version, datetime.now(timezone.utc).isoformat(),
        _csv_audit(records_path, rec_rel, record_schema, record_rows, record_bytes, record_encoding),
        _csv_audit(pairs_path, pair_rel, pair_schema, pair_rows, pair_bytes, pair_encoding),
        duplicate_record_ids, tuple(sorted(duplicate_pairs)), tuple(sorted(reversed_pairs)),
        tuple(self_pairs), tuple(dangling), tuple(invalid_labels), tuple(sorted(conflicting)),
        tuple(entity_conflicts), dict(sorted(sources.items())), dict(sorted(entities.items())),
        dict(sorted(entity_count_dist.items())), same_source_duplicate_groups,
        dict(sorted(pair_splits.items())), dict(sorted(pair_labels.items())),
        {key: dict(sorted(value.items())) for key, value in sorted(pair_source_dist.items())},
        dict(sorted(pair_cases.items())), dict(sorted(hard_negative_types.items())), record_leaks,
        pair_leaks, actual_features,
        "Opaque join key only: dataset::source::raw_id; IDs are not features or tokens.",
    )


def dataset_manifest(audit: MasterOneAudit) -> dict[str, Any]:
    benchmark_root = Path(__file__).resolve().parents[2]
    dataset_config = benchmark_root / "standalone" / "config" / "datasets" / "masterone_test.json"
    config_digest = config_hash(json.loads(dataset_config.read_text(encoding="utf-8")))
    return {
        "manifest_schema": "masterone-dataset-manifest-v1",
        "dataset_id": "masterone_test",
        "dataset_version": audit.dataset_version,
        "schema_version": "1",
        "config_hash": config_digest,
        "task": "multi_source",
        "sources": list(audit.source_distribution),
        "allowed_source_pairs": [list(pair) for pair in combinations_with_replacement(sorted(audit.source_distribution), 2)],
        "pair_universe_policy": "All same-source and cross-source pairs remain legal; source uniqueness has not been verified.",
        "gold_coverage": {
            "pair_labels": "sampled_labeled_pairs",
            "entity_labels": None,
            "entity_labels_verified": False,
            "reason": "Only 1,200 listed test pairs are labeled; entity_id is generator provenance and has not been independently validated.",
        },
        "entity_distribution": {
            "records_with_entity_id": sum(count for entity, count in audit.entity_distribution.items() if entity != "<empty>"),
            "distinct_entity_ids": sum(entity != "<empty>" for entity in audit.entity_distribution),
            "entity_records_per_source_group": audit.entity_count_distribution,
            "repeated_same_source_entity_groups": audit.same_source_duplicate_entity_groups,
            "verified": False,
        },
        "listed_pair_source_distribution": audit.pair_source_distribution,
        "pair_case_distribution": audit.pair_case_distribution,
        "hard_negative_type_distribution": audit.hard_negative_type_distribution,
        "feature_allowlist": list(audit.feature_allowlist),
        "record_id_policy": audit.record_id_policy,
        "files": [asdict(audit.records), asdict(audit.pairs)],
        "provenance": "Internal team-generated dataset; generator/label adjudication provenance not present in the audited files.",
        "generated_at_utc": audit.generated_at_utc,
        "raw_sources_immutable": True,
    }
