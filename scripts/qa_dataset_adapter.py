"""Raw-only GenVN 10k inventory/CSV decoder for QA, never a truth input.

The native-file decoding rules follow GenVN src/benchmark/files.py: physical
header/total rows are counted separately; malformed rows get explicit counts.
This is a benchmark boundary, not native MasterOne XLSX/CP1258 support.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path

import yaml
from openpyxl import load_workbook

FIELDS = ("source_record_id", "source_system", "entity_type", "name", "phone", "email",
          "dob", "address", "government_id", "government_id_type", "source_updated_at", "is_active")
EXTRA = ("qa_record_key", "qa_raw_file", "qa_raw_row", "qa_batch_id", "qa_original_fields")


def decoded_rows(path, spec):
    if spec["format"] == "xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            yield from workbook.active.values
        finally:
            workbook.close()
    elif spec.get("mixed_encoding"):
        # The declared mixed encoding is a decoder contract, never ignore bytes.
        with path.open("rb") as stream:
            def lines():
                for line in stream:
                    try:
                        yield line.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        yield line.decode("cp1258", errors="strict")
            yield from csv.reader(lines(), delimiter=spec["delimiter"], strict=True)
    else:
        with path.open(encoding=spec["encoding"], newline="", errors="strict") as stream:
            yield from csv.reader(stream, delimiter=spec["delimiter"], strict=True)


def adapt(dataset: Path, output: Path):
    if dataset.parent.name != "10k":
        raise ValueError("Only the authorized 10k datasets may be read")
    raw = (dataset / "raw").resolve()
    catalog = yaml.safe_load((raw / "source_mapping.yaml").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    writers, handles, sources, errors = {}, {}, Counter(), Counter()
    identities, duplicates, missing_names, supplemental = set(), 0, 0, 0
    hashes = {}
    try:
        for spec in catalog["files"]:
            relative = Path(spec["path"])
            path = (dataset / relative).resolve()
            if not path.is_relative_to(raw):
                raise ValueError("Source mapping escapes raw directory")
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            rows = iter(decoded_rows(path, spec))
            for _ in range(spec["header_row"]):
                next(rows)
            header = list(next(rows))
            if any(spec["columns"][field] not in header for field in FIELDS):
                raise ValueError("Source header disagrees with declared mapping")
            for number, values in enumerate(rows, start=spec["header_row"] + 2):
                if list(values) == header or (values and unicodedata.normalize("NFC", str(values[0])) == "Tổng cộng" and not any(values[1:])):
                    supplemental += 1
                    continue
                if len(values) != len(header):
                    errors["MALFORMED_ROW"] += 1
                    continue
                original = {key: "" if value is None else str(value) for key, value in zip(header, values, strict=True)}
                row = {field: original[spec["columns"][field]] for field in FIELDS}
                if not row["source_record_id"].strip():
                    errors["MISSING_SOURCE_RECORD_ID"] += 1
                    continue
                source = row["source_system"]
                key = f"{source}:{row['source_record_id']}:{spec['batch_id']}"
                identity = (source, row["source_record_id"])
                duplicates += identity in identities
                identities.add(identity)
                missing_names += not row["name"].strip()
                sources[source] += 1
                # Preserve the original source and identifier, even if the API
                # rejects them. No invented name/ID or truth-based correction.
                if source not in writers:
                    handle = (output / f"{source}.csv").open("w", encoding="utf-8", newline="")
                    handles[source] = handle
                    writers[source] = csv.DictWriter(handle, fieldnames=(*FIELDS, *EXTRA))
                    writers[source].writeheader()
                writers[source].writerow({**row, "qa_record_key": key, "qa_raw_file": relative.as_posix(),
                    "qa_raw_row": number, "qa_batch_id": spec["batch_id"],
                    "qa_original_fields": json.dumps(original, ensure_ascii=False, separators=(",", ":"))})
    finally:
        for handle in handles.values():
            handle.close()
    report = {"dataset": dataset.parent.parent.name, "scale": "10k", "raw_files": len(catalog["files"]),
        "decoded_records": sum(sources.values()), "rejected_records": sum(errors.values()),
        "rejection_reasons": dict(errors), "supplemental_physical_rows": supplemental,
        "source_counts": dict(sources), "duplicate_source_ids_across_batches": duplicates,
        "missing_required_names": missing_names, "raw_sha256": hashes,
        "truth_accessed": False, "boundary": "raw decoder and concatenation only; no matching/label correction"}
    (output / "inventory.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = [adapt(dataset / "10k/seed-7", args.output / dataset.name)
               for dataset in sorted(args.root.glob("TC*")) if dataset.is_dir()]
    print(json.dumps([{k: v for k, v in report.items() if k != "raw_sha256"} for report in reports], indent=2))


if __name__ == "__main__":
    main()
