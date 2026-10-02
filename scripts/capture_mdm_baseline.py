"""Capture a deterministic baseline for the current backend MDM matcher.

This diagnostic script deliberately does not change production code. It uses the
same CSV parser and PrototypeMatcher as the API, then assigns stable in-memory
record IDs in the API's current input order: CRM, ERP, BILL.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile

from masterone.mdm.import_service import read_csv
from masterone.mdm.matching import PrototypeMatcher


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_DIR = ROOT / "data" / "golden" / "mdm"
DEFAULT_OUTPUT = ROOT / "artifacts" / "baseline" / "mdm_exact_signal_v1.json"
SOURCE_ORDER = ("CRM", "ERP", "BILL")


def _upload(path: Path) -> UploadFile:
    return UploadFile(filename=path.name, file=BytesIO(path.read_bytes()))


async def _load_records(golden_dir: Path):
    records = []
    profiles = {}
    for source in SOURCE_ORDER:
        path = golden_dir / f"{source.lower()}.csv"
        source_records, profile = await read_csv(_upload(path), source)
        records.extend(source_records)
        profiles[source] = profile

    # The production model currently generates UUIDs. Stable IDs here are only
    # for comparison and reflect the existing concatenation order.
    for index, record in enumerate(records, start=1):
        record.id = f"r{index:04d}"
        record.import_id = "baseline-import"
    return records, profiles


def _connected_components(records, candidates):
    parent = {record.id: record.id for record in records}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for candidate in candidates:
        union(candidate.left_record_id, candidate.right_record_id)

    groups = defaultdict(list)
    for record in records:
        groups[find(record.id)].append(record.id)
    return sorted((sorted(ids) for ids in groups.values()), key=lambda ids: ids[0])


async def capture(golden_dir: Path, output_path: Path) -> None:
    records, profiles = await _load_records(golden_dir)
    candidates = PrototypeMatcher().match(records)
    by_id = {record.id: record for record in records}

    pairs = []
    counts = defaultdict(int)
    for candidate in candidates:
        left = by_id[candidate.left_record_id]
        right = by_id[candidate.right_record_id]
        pair = {
            "id_a": candidate.left_record_id,
            "id_b": candidate.right_record_id,
            "source_record_a": left.source_record_id,
            "source_record_b": right.source_record_id,
            "source_a": left.source,
            "source_b": right.source,
            "evidence": sorted(candidate.evidence, key=lambda item: (item["field"], item["value"])),
        }
        pairs.append(pair)
        for evidence in candidate.evidence:
            counts[evidence["field"]] += 1

    pairs.sort(key=lambda pair: (pair["id_a"], pair["id_b"]))
    input_fingerprint = hashlib.sha256()
    for source in SOURCE_ORDER:
        input_fingerprint.update((golden_dir / f"{source.lower()}.csv").read_bytes())

    payload = {
        "baseline_version": "mdm-exact-signal-v1",
        "source_order": list(SOURCE_ORDER),
        "matcher_engine": PrototypeMatcher.engine_id,
        "matcher_fields": list(PrototypeMatcher.fields),
        "candidate_limit": 1000,
        "input_fingerprint_sha256": input_fingerprint.hexdigest(),
        "profiles": profiles,
        "row_count": len(records),
        "record_ids": [
            {
                "id": record.id,
                "source": record.source,
                "source_record_id": record.source_record_id,
                "field_types": {key: type(value).__name__ for key, value in record.raw_fields.items()},
            }
            for record in records
        ],
        "candidate_count": len(pairs),
        "candidate_counts_by_evidence_field": dict(sorted(counts.items())),
        "pairs": pairs,
        "master_grouping": _connected_components(records, candidates),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "rows": len(records), "pairs": len(pairs), "groups": len(payload["master_grouping"])}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(capture(args.golden_dir, args.output))


if __name__ == "__main__":
    main()
