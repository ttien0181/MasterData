"""Loader for benchmark-local Vietnam reference files; .bak files are never scanned."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from standalone.contracts.interfaces import MappingCandidate, MappingResolution, ReferenceProvider
from standalone.runtime.paths import resolve_benchmark_path

FILES = {
    "column_aliases": Path("datasets/data_reference/config/column_alias_dictionary_v1.csv"),
    "units": Path("datasets/data_reference/reference/administrative_mapping/administrative_dictionary_v1.csv"),
    "admin_aliases": Path("datasets/data_reference/reference/administrative_mapping/administrative_aliases_v1.csv"),
    "old_to_new": Path("datasets/data_reference/reference/administrative_mapping/mapping_entries_v1.json"),
}


def _csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"reference CSV has invalid header: {path}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"reference CSV has malformed row width: {path}")
    return list(reader.fieldnames), rows


def _fold(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True)
class AdministrativeUnit:
    unit_id: str
    unit_name: str
    unit_level: str
    parent_id: str | None
    admin_version: str
    effective_date: date | None


@dataclass(frozen=True)
class ReferenceFileInfo:
    relative_path: str
    sha256: str
    encoding: str
    row_count: int
    schema: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceBundle:
    version: str
    column_aliases: tuple[Mapping[str, str], ...]
    administrative_aliases: tuple[Mapping[str, str], ...]
    units: tuple[AdministrativeUnit, ...]
    mappings: tuple[Mapping[str, object], ...]
    files: tuple[ReferenceFileInfo, ...]


class LocalReferenceProvider(ReferenceProvider):
    def __init__(self, bundle: ReferenceBundle) -> None:
        self.bundle = bundle
        self._column_aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in bundle.column_aliases:
            self._column_aliases[(_fold(row["alias"]), row["version"])].add(row["canonical_field"])
        self._aliases: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._units: dict[tuple[str, str], AdministrativeUnit] = {}
        for unit in bundle.units:
            self._units[(unit.admin_version, unit.unit_id)] = unit
        for row in bundle.administrative_aliases:
            self._aliases[(_fold(row["alias"]), row["admin_version"])].add(row["unit_id"])
        for unit in bundle.units:
            self._aliases[(_fold(unit.unit_name), unit.admin_version)].add(unit.unit_id)

    @property
    def version(self) -> str:
        return self.bundle.version

    def resolve_column_alias(self, alias: str, version: str | None = None) -> tuple[str, ...]:
        folded = _fold(alias)
        if version is not None:
            return tuple(sorted(self._column_aliases.get((folded, version), set())))
        return tuple(sorted({
            field for (known_alias, _known_version), fields in self._column_aliases.items()
            if known_alias == folded for field in fields
        }))

    def resolve_alias(self, alias: str, admin_version: str | None = None) -> tuple[MappingCandidate, ...]:
        folded = _fold(alias)
        if admin_version is not None:
            keys = {(admin_version, unit_id) for unit_id in self._aliases.get((folded, admin_version), set())}
        else:
            keys: set[tuple[str, str]] = set()
            for (known_alias, version), unit_ids in self._aliases.items():
                if known_alias == folded:
                    keys.update((version, unit_id) for unit_id in unit_ids)
        candidates = []
        for key in sorted(keys):
            unit = self._units.get(key)
            if unit is not None:
                candidates.append(MappingCandidate(unit.unit_id, unit.unit_name, unit.unit_level, unit.admin_version))
        return tuple(candidates)

    def map_old_to_new(self, source_unit_id: str, source_admin_version: str | None = None,
                       as_of: date | None = None) -> MappingResolution:
        if as_of is not None and not isinstance(as_of, date):
            raise ValueError("as_of must be a date")
        matched = [row for row in self.bundle.mappings
                   if str(row.get("source_unit_id", "")) == source_unit_id
                   and (source_admin_version is None or str(row.get("source_admin_version", "")) == source_admin_version)]
        if not matched:
            return MappingResolution("unmapped", ())
        if as_of is not None:
            dated = [row for row in matched if row.get("effective_date")]
            eligible = [row for row in dated if date.fromisoformat(str(row["effective_date"])) <= as_of]
            if not eligible:
                return MappingResolution("unknown_effective_date" if len(dated) < len(matched)
                                         else "not_effective", ())
            matched = eligible
        candidates: dict[tuple[str, str], MappingCandidate] = {}
        evidence: list[str] = []
        statuses: set[str] = set()
        for row in matched:
            statuses.add(str(row.get("mapping_status", "unknown")))
            for item in row.get("target_units", []) or []:
                if not isinstance(item, dict) or not item.get("unit_id"):
                    continue
                uid = str(item["unit_id"])
                version = str(row.get("target_admin_version", ""))
                candidates[(version, uid)] = MappingCandidate(
                    uid, str(item.get("unit_name", "")),
                    None if item.get("unit_level") is None else str(item["unit_level"]), version or None,
                )
            if not row.get("target_units") and row.get("target_unit_id"):
                uid = str(row["target_unit_id"])
                version = str(row.get("target_admin_version", ""))
                candidates[(version, uid)] = MappingCandidate(
                    uid, str(row.get("target_unit_name", "")), admin_version=version or None,
                )
            for item in row.get("evidence", []) or []:
                if isinstance(item, dict):
                    evidence.append(" / ".join(str(item.get(key, "")) for key in ("source", "reference", "effective_date") if item.get(key)))
        # Ambiguity is conservative: multiple targets or any explicit ambiguous status stays ambiguous.
        source_versions = {str(row.get("source_admin_version", "")) for row in matched}
        status = "ambiguous" if (len(candidates) > 1 or "ambiguous" in statuses
                                 or len(source_versions) > 1) else next(iter(statuses), "unknown")
        return MappingResolution(status, tuple(candidates[key] for key in sorted(candidates)), tuple(evidence))


def load_local_references() -> tuple[ReferenceBundle, LocalReferenceProvider]:
    paths = {name: resolve_benchmark_path(path) for name, path in FILES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"reference source file(s) missing: {missing}")
    col_schema, col_rows = _csv(paths["column_aliases"])
    unit_schema, unit_rows = _csv(paths["units"])
    alias_schema, alias_rows = _csv(paths["admin_aliases"])
    expected = {
        "column_aliases": {"canonical_field", "alias", "language", "alias_type", "version"},
        "units": {"unit_id", "unit_name", "unit_level", "parent_id", "admin_version", "effective_date"},
        "admin_aliases": {"unit_id", "alias", "alias_type", "admin_version"},
    }
    for name, schema in (("column_aliases", col_schema), ("units", unit_schema), ("admin_aliases", alias_schema)):
        if not expected[name].issubset(schema):
            raise ValueError(f"reference {name} schema missing columns: {sorted(expected[name] - set(schema))}")
    with paths["old_to_new"].open("r", encoding="utf-8-sig") as handle:
        mapping_rows = json.load(handle)
    if not isinstance(mapping_rows, list) or any(not isinstance(row, dict) for row in mapping_rows):
        raise ValueError("old-to-new mapping JSON must be a list of objects")

    units: list[AdministrativeUnit] = []
    for row_no, row in enumerate(unit_rows, 2):
        raw_date = row.get("effective_date", "").strip()
        try:
            effective = date.fromisoformat(raw_date) if raw_date else None
        except ValueError as exc:
            raise ValueError(f"administrative unit row {row_no} has invalid effective_date {raw_date!r}") from exc
        units.append(AdministrativeUnit(
            row["unit_id"].strip(), row["unit_name"].strip(), row["unit_level"].strip(),
            row["parent_id"].strip() or None, row["admin_version"].strip(), effective,
        ))
    infos: list[ReferenceFileInfo] = []
    for name in FILES:
        path = paths[name]
        data = path.read_bytes()
        if name == "column_aliases": schema, count = col_schema, len(col_rows)
        elif name == "units": schema, count = unit_schema, len(unit_rows)
        elif name == "admin_aliases": schema, count = alias_schema, len(alias_rows)
        else: schema, count = ("JSON-list[object]",), len(mapping_rows)
        infos.append(ReferenceFileInfo(FILES[name].as_posix(), hashlib.sha256(data).hexdigest(),
                                       "UTF-8 with BOM" if data.startswith(b"\xef\xbb\xbf") else "UTF-8",
                                       count, tuple(schema)))
    digest = hashlib.sha256("".join(info.sha256 for info in infos).encode("ascii")).hexdigest()
    bundle = ReferenceBundle(
        f"local-reference-sha256:{digest}", tuple(col_rows), tuple(alias_rows), tuple(units),
        tuple(mapping_rows), tuple(infos),
    )
    validate_reference_bundle(bundle)
    return bundle, LocalReferenceProvider(bundle)


def validate_reference_bundle(bundle: ReferenceBundle) -> None:
    """Reject unusable mapping rows while preserving legitimate alias ambiguity."""
    audit = audit_reference_bundle(bundle)
    critical = (
        "duplicate_unit_keys", "dangling_parent_rows", "mapping_source_units_missing",
        "mapping_target_units_missing", "duplicate_mapping_rows",
        "administrative_alias_units_missing", "invalid_mapping_dates",
    )
    failures = [name for name in critical if audit[name]]
    if failures:
        raise ValueError(f"reference integrity failure: {', '.join(failures)}")
    unit_by_key = {(unit.admin_version, unit.unit_id): unit for unit in bundle.units}
    versions = {unit.admin_version for unit in bundle.units}
    for index, row in enumerate(bundle.mappings, 1):
        source_version = row.get("source_admin_version")
        target_version = row.get("target_admin_version")
        if not isinstance(source_version, str) or source_version not in versions:
            raise ValueError(f"mapping row {index} has invalid source admin version")
        if not isinstance(target_version, str) or target_version not in versions:
            raise ValueError(f"mapping row {index} has invalid target admin version")
        if source_version == target_version or row.get("direction") != "old_to_new":
            raise ValueError(f"mapping row {index} has invalid version/direction for old-to-new mapping")
        if row.get("mapping_status") not in {"resolved", "ambiguous"}:
            raise ValueError(f"mapping row {index} has invalid mapping status")
        if not row.get("mapping_dataset_version"):
            raise ValueError(f"mapping row {index} has no mapping dataset version")
        targets = row.get("target_units")
        if not isinstance(targets, list) or not targets or any(
                not isinstance(item, dict) or not item.get("unit_id") for item in targets):
            raise ValueError(f"mapping row {index} has invalid target units")
        if not row.get("effective_date"):
            raise ValueError(f"mapping row {index} has no effective date")
        mapping_date = date.fromisoformat(str(row["effective_date"]))
        for target in targets:
            unit = unit_by_key[(target_version, str(target["unit_id"]))]
            if unit.effective_date and unit.effective_date != mapping_date:
                raise ValueError(f"mapping row {index} date differs from target unit effective date")


def audit_reference_bundle(bundle: ReferenceBundle) -> dict[str, object]:
    unit_by_key = {(unit.admin_version, unit.unit_id): unit for unit in bundle.units}
    duplicate_unit_keys: list[str] = []
    unit_counts: dict[tuple[str, str], int] = defaultdict(int)
    for unit in bundle.units:
        unit_counts[(unit.admin_version, unit.unit_id)] += 1
    duplicate_unit_keys = [f"{version}:{unit_id}" for (version, unit_id), count in sorted(unit_counts.items()) if count > 1]
    dangling_parents = [f"{u.admin_version}:{u.unit_id}->{u.parent_id}" for u in bundle.units
                        if u.parent_id and (u.admin_version, u.parent_id) not in unit_by_key]
    source_unit_missing: list[str] = []
    target_unit_missing: list[str] = []
    duplicate_mappings: list[str] = []
    mapping_keys: dict[tuple[str, str, str, str], int] = defaultdict(int)
    ambiguous_count = 0
    for row in bundle.mappings:
        source_id = str(row.get("source_unit_id", ""))
        source_version = str(row.get("source_admin_version", ""))
        target_version = str(row.get("target_admin_version", ""))
        if source_version and (source_version, source_id) not in unit_by_key:
            source_unit_missing.append(f"{source_version}:{source_id}")
        if str(row.get("mapping_status", "")).lower() == "ambiguous":
            ambiguous_count += 1
        target_ids = []
        for target in row.get("target_units", []) or []:
            if not isinstance(target, dict) or not target.get("unit_id"):
                continue
            target_id = str(target["unit_id"])
            target_ids.append(target_id)
            if target_version and (target_version, target_id) not in unit_by_key:
                target_unit_missing.append(f"{target_version}:{target_id}")
        key = (source_version, source_id, target_version, ",".join(sorted(target_ids)))
        mapping_keys[key] += 1
    duplicate_mappings = ["|".join(key) for key, count in sorted(mapping_keys.items()) if count > 1]
    alias_to_units: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in bundle.administrative_aliases:
        alias_to_units[(_fold(row.get("alias", "")), row.get("admin_version", ""))].add(row.get("unit_id", ""))
    alias_collisions = [f"{version}:{alias}" for (alias, version), ids in sorted(alias_to_units.items()) if len(ids) > 1]
    alias_units_missing = sorted({
        f"{row.get('admin_version', '')}:{row.get('unit_id', '')}"
        for row in bundle.administrative_aliases
        if (row.get("admin_version", ""), row.get("unit_id", "")) not in unit_by_key
    })
    column_alias_targets: dict[str, set[str]] = defaultdict(set)
    for row in bundle.column_aliases:
        column_alias_targets[_fold(row.get("alias", ""))].add(row.get("canonical_field", ""))
    column_alias_collisions = [alias for alias, fields in sorted(column_alias_targets.items()) if len(fields) > 1]
    invalid_mapping_dates: list[str] = []
    mapping_directions: Counter[str] = Counter()
    target_count_distribution: Counter[str] = Counter()
    for index, row in enumerate(bundle.mappings):
        mapping_directions[str(row.get("direction", "unknown"))] += 1
        targets = row.get("target_units", []) or []
        target_count_distribution[str(len(targets))] += 1
        raw_date = str(row.get("effective_date", "")).strip()
        if raw_date:
            try:
                date.fromisoformat(raw_date)
            except ValueError:
                invalid_mapping_dates.append(f"row:{index + 1}:{raw_date}")
    return {
        "reference_version": bundle.version,
        "file_manifests": [asdict(file_info) for file_info in bundle.files],
        "column_alias_rows": len(bundle.column_aliases),
        "administrative_alias_rows": len(bundle.administrative_aliases),
        "administrative_unit_rows": len(bundle.units),
        "units_by_version": dict(sorted(Counter(unit.admin_version for unit in bundle.units).items())),
        "mapping_rows": len(bundle.mappings),
        "mapping_status_counts": dict(sorted(Counter(str(row.get("mapping_status", "unknown")) for row in bundle.mappings).items())),
        "ambiguous_mapping_rows": ambiguous_count,
        "duplicate_unit_keys": duplicate_unit_keys,
        "dangling_parent_rows": dangling_parents,
        "mapping_source_units_missing": sorted(set(source_unit_missing)),
        "mapping_target_units_missing": sorted(set(target_unit_missing)),
        "duplicate_mapping_rows": duplicate_mappings,
        "administrative_alias_collisions": alias_collisions,
        "administrative_alias_units_missing": alias_units_missing,
        "column_alias_collisions": column_alias_collisions,
        "invalid_mapping_dates": invalid_mapping_dates,
        "mapping_direction_counts": dict(sorted(mapping_directions.items())),
        "target_count_distribution": dict(sorted(target_count_distribution.items())),
        "source_provenance": "Local source files; upstream source/license provenance has not been independently verified.",
        "bak_files_loaded": False,
    }
