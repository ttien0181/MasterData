"""Audit current local data and write manifests/reports without modifying raw inputs."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCHMARK_ROOT))

from standalone.datasets.audit import audit_masterone, dataset_manifest  # noqa: E402
from standalone.datasets.masterone import MasterOneAdapter  # noqa: E402
from standalone.datasets.universe import LegalPairUniverse  # noqa: E402
from standalone.references.local import audit_reference_bundle, load_local_references  # noqa: E402
from standalone.runtime.paths import PathSafetyError, resolve_benchmark_path  # noqa: E402


def _artifact_path(path: Path, subdirectory: str, *, create_parent: bool = False) -> Path:
    scope = BENCHMARK_ROOT / subdirectory
    if scope.resolve(strict=False) != scope:
        raise PathSafetyError(f"audit output directory is redirected: {scope}")
    resolved = resolve_benchmark_path(path)
    try:
        resolved.relative_to(scope)
    except ValueError:
        raise PathSafetyError(f"audit artifact must remain under {scope}: {resolved}") from None
    if create_parent:
        resolved = resolve_benchmark_path(resolved, create_parent=True)
        try:
            resolved.relative_to(scope)
        except ValueError:
            raise PathSafetyError(f"audit artifact parent escaped {scope}: {resolved}") from None
    return resolved


def _dump(path: Path, value: Any) -> None:
    path = _artifact_path(path, "datasets/manifests")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        old_compare = dict(existing)
        new_compare = json.loads(json.dumps(value, ensure_ascii=False))
        old_compare.pop("generated_at_utc", None)
        new_compare.pop("generated_at_utc", None)
        if old_compare != new_compare:
            diff = [key for key in sorted(set(old_compare) | set(new_compare))
                    if old_compare.get(key) != new_compare.get(key)]
            raise RuntimeError(f"frozen manifest differs at {path} in field(s) {diff}; create a new dataset/reference version")
        return
    path = _artifact_path(path, "datasets/manifests", create_parent=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_audit_doc(path: Path, content: str) -> None:
    path = _artifact_path(path, "docs")
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        old_lines = existing.splitlines()
        new_lines = content.splitlines()
        old_lines = ["Audit time (UTC): <frozen>" if line.startswith("Audit time (UTC):") else line for line in old_lines]
        new_lines = ["Audit time (UTC): <frozen>" if line.startswith("Audit time (UTC):") else line for line in new_lines]
        if old_lines != new_lines:
            raise RuntimeError(f"audit report differs at {path}; review and version the audit artifact explicitly")
        return
    path = _artifact_path(path, "docs", create_parent=True)
    path.write_text(content, encoding="utf-8")


def _render_report(audit: Any, references: dict[str, Any]) -> str:
    lines = [
        "# Dataset audit — MasterOne G1", "",
        f"Audit time (UTC): `{audit.generated_at_utc}`", "",
        "## Internal MasterOne test data", "",
        "Raw files were read-only. The manifest records their SHA-256, UTF-8 encoding, schema and row counts.", "",
        "| File | Rows | Columns | SHA-256 |", "| --- | ---: | ---: | --- |",
    ]
    for item in (audit.records, audit.pairs):
        lines.append(f"| `{item.relative_path}` | {item.row_count:,} | {len(item.schema)} | `{item.sha256}` |")
    lines.extend([
        "", f"Structural audit status: **{'PASS' if audit.structurally_valid else 'ISSUES FOUND'}**.",
        "", "### Dataset profile", "",
        "| Check | Observed |", "| --- | --- |",
        f"| Sources | `{audit.source_distribution}` |",
        f"| Distinct non-empty entity labels | {sum(n for key, n in audit.entity_distribution.items() if key != '<empty>')} record rows across {sum(key != '<empty>' for key in audit.entity_distribution)} entity IDs |",
        f"| Pair labels | `{audit.pair_label_distribution}` |",
        f"| Pair split | `{audit.pair_split_distribution}` |",
        f"| Pair source slices | `{audit.pair_source_distribution}` |",
        f"| Pair case types | `{audit.pair_case_distribution}` |",
        f"| Hard negative types | `{audit.hard_negative_type_distribution}` |",
        f"| Entity/source groups with repeated records | {audit.same_source_duplicate_entity_groups} |",
        f"| Entity count per entity/source group | `{audit.entity_count_distribution}` |",
        f"| Duplicate record IDs | {len(audit.duplicate_record_ids)} |",
        f"| Duplicate / reversed pair keys | {len(audit.duplicate_pairs)} / {len(audit.reversed_duplicate_pairs)} |",
        f"| Self-pairs / dangling rows / invalid labels | {len(audit.self_pairs)} / {len(audit.dangling_pair_rows)} / {len(audit.invalid_label_rows)} |",
        f"| Duplicate-key label conflicts | {len(audit.conflicting_pair_labels)} |",
        f"| Pair label vs entity_id consistency conflicts | {len(audit.entity_pair_label_conflicts)} |",
        "", "### Leakage controls", "",
        f"Feature allowlist: `{', '.join(audit.feature_allowlist)}`.",
        "Raw ID is only a join/trace key and is never tokenized or compared. `entity_id`, `address_id`, ",
        "`noise_type`, `difficulty`, `temporal_case`, and pair annotations are retained in gold/evaluation ",
        "structures, not in scorer-facing `Record` or `RecordFeatures`.", "",
        f"Columns present in records that require gold/evaluation isolation: `{', '.join(audit.record_leakage_columns) or '(none)'}`.",
        f"Columns present in pairs that require gold/evaluation isolation: `{', '.join(audit.pair_leakage_columns) or '(none)'}`.",
        "", "### Split finding", "",
        "All listed pair rows currently use the `test` split. There is no independent train or dev partition ",
        "in this dataset. Do not split these test pairs for training/tuning; such experiments are BLOCKED.",
        "The presence of `entity_id` values is generator-provided and is not treated as independently verified complete entity gold.",
        "", "## Local address references", "",
        f"Reference bundle version: `{references['reference_version']}`.",
        "The `.bak` backup was not loaded. Column aliases and administrative aliases are separate tables; ",
        "unit rows and old-to-new mappings are separate. Ambiguous mappings preserve all candidate units.", "",
        f"Rows: column aliases {references['column_alias_rows']:,}; administrative aliases {references['administrative_alias_rows']:,}; ",
        f"units {references['administrative_unit_rows']:,}; old-to-new mappings {references['mapping_rows']:,}.",
        f"Mapping status counts: `{references['mapping_status_counts']}`.",
        f"Reference integrity issue counts: duplicate unit keys {len(references['duplicate_unit_keys'])}, ",
        f"dangling parents {len(references['dangling_parent_rows'])}, missing mapping sources {len(references['mapping_source_units_missing'])}, ",
        f"missing mapping targets {len(references['mapping_target_units_missing'])}, duplicate mappings {len(references['duplicate_mapping_rows'])}, ",
        f"missing alias units {len(references['administrative_alias_units_missing'])}, ",
        f"administrative alias collisions {len(references['administrative_alias_collisions'])}.", "",
        f"Column alias collisions {len(references['column_alias_collisions'])}; invalid mapping dates {len(references['invalid_mapping_dates'])}.",
        f"Mapping directions: `{references['mapping_direction_counts']}`; target count distribution: `{references['target_count_distribution']}`.",
        "Upstream source/license provenance for local reference files has not been independently verified.", "",
        "Detailed hashes, schemas and integrity findings are in `datasets/manifests/`.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    dataset_dir = resolve_benchmark_path(BENCHMARK_ROOT / "datasets" / "masterone_test")
    records_path = resolve_benchmark_path(dataset_dir / "records.csv")
    pairs_path = resolve_benchmark_path(dataset_dir / "pairs.csv")
    audit = audit_masterone(records_path, pairs_path)
    if not audit.structurally_valid:
        print("DATA AUDIT FAILED: structural defects in the raw dataset; no manifest was written", file=sys.stderr)
        return 2
    # The audit can bootstrap a first manifest; _dump below rejects any drift
    # once a frozen manifest exists. Ordinary adapter loads require the manifest.
    bundle = MasterOneAdapter(dataset_dir, require_manifest=False).load()
    universe = LegalPairUniverse.from_records(bundle.records, bundle.metadata)
    _, provider = load_local_references()
    ref_audit = audit_reference_bundle(provider.bundle)
    manifest = dataset_manifest(audit)
    manifest["legal_pair_universe"] = {
        "policy": "same-source and cross-source pairs; source uniqueness has not been verified",
        "possible_pair_count": universe.pair_count,
    }
    _dump(BENCHMARK_ROOT / "datasets" / "manifests" / "masterone_test_v1.json", manifest)
    _dump(BENCHMARK_ROOT / "datasets" / "manifests" / "data_reference_v1.json", ref_audit)
    report = _render_report(audit, ref_audit)
    _write_audit_doc(BENCHMARK_ROOT / "docs" / "dataset_audit.md", report)
    print(f"MasterOne audit: {audit.records.row_count} records; {audit.pairs.row_count} listed pairs; "
          f"legal universe: {universe.pair_count}; structural issues: {not audit.structurally_valid}")
    print(f"Reference bundle: {provider.version}; .bak loaded: {ref_audit['bak_files_loaded']}")
    print("Wrote or verified frozen benchmark/datasets/manifests/*_v1.json and benchmark/docs/dataset_audit.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
