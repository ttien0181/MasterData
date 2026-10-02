"""Stream and merge CSV objects from Cloudflare R2 using their actual headers."""

import csv
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from masterone.pipeline.r2 import create_r2_client

logger = logging.getLogger(__name__)


class CsvMergeError(ValueError):
    """Raised when an input CSV cannot be safely merged."""


def _normalize_header(header: list[str], object_key: str) -> tuple[str, ...]:
    columns = [column.strip() for column in header]
    if not columns or any(not column for column in columns):
        raise CsvMergeError(f"CSV header contains an empty column name: {object_key}")
    if "record_id" in columns and "source_record_id" in columns:
        raise CsvMergeError(
            f"{object_key} must use only one ID header: record_id or source_record_id"
        )
    columns = ["record_id" if column == "source_record_id" else column for column in columns]
    if len(set(columns)) != len(columns):
        raise CsvMergeError(f"CSV header contains duplicate column names: {object_key}")
    return tuple(columns)


def get_csv_columns(client, bucket: str, object_key: str) -> tuple[str, ...]:
    """Read and normalize only the CSV header from an R2 object."""
    response = client.get_object(Bucket=bucket, Key=object_key)
    body = response["Body"]
    try:
        reader = csv.reader(line.decode("utf-8-sig") for line in body.iter_lines())
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvMergeError(f"Input CSV is empty: {object_key}") from exc
        columns = _normalize_header(raw_header, object_key)
        logger.info("Read %s columns from %s", len(columns), object_key)
        return columns
    except UnicodeDecodeError as exc:
        raise CsvMergeError(f"CSV header is not valid UTF-8: {object_key}") from exc
    finally:
        body.close()


def build_merged_schema(source_columns: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Put columns shared by every input first, then append remaining columns once."""
    if not source_columns:
        raise CsvMergeError("At least one source header is required")

    common = set(source_columns[0])
    for columns in source_columns[1:]:
        common.intersection_update(columns)

    ordered_common = [column for column in source_columns[0] if column in common]
    merged = list(ordered_common)
    seen = set(ordered_common)
    for columns in source_columns:
        for column in columns:
            if column not in seen:
                merged.append(column)
                seen.add(column)
    return tuple(merged)


def _source_label(object_key: str) -> str:
    return Path(object_key).name


def merge_csv_objects(
    *, endpoint_url: str, access_key_id: str, secret_access_key: str, bucket: str,
    object_keys: list[str], cleanup_sources: bool = True, output_key: str | None = None,
) -> dict[str, object]:
    """Merge R2 CSV objects with dynamic columns and disk-backed streaming."""
    if not 1 <= len(object_keys) <= 3:
        raise CsvMergeError("Exactly 1 to 3 source files are supported")
    if len(set(object_keys)) != len(object_keys):
        raise CsvMergeError("Duplicate object keys are not allowed")
    if any(not key.startswith("incoming/") or key.endswith("/") for key in object_keys):
        raise CsvMergeError("Only objects under incoming/ can be merged")

    client = create_r2_client(endpoint_url, access_key_id, secret_access_key)
    source_columns = [get_csv_columns(client, bucket, key) for key in object_keys]
    merged_schema = build_merged_schema(source_columns)
    output_key = output_key or f"merged/{uuid4()}.csv"
    if output_key.endswith("/") or output_key.startswith("incoming/"):
        raise CsvMergeError("Output object key must be a file outside incoming/")
    rows_written = 0
    source_stats: list[dict[str, object]] = []

    with NamedTemporaryFile(mode="w+", encoding="utf-8", newline="", suffix=".csv", delete=False) as output_file:
        output_path = Path(output_file.name)
        try:
            writer = csv.writer(output_file, lineterminator="\n")
            writer.writerow(merged_schema)
            for object_key, expected_columns in zip(object_keys, source_columns, strict=True):
                source_rows = 0
                with NamedTemporaryFile(mode="w+b", suffix=".csv") as input_file:
                    logger.info("Downloading R2 object %s", object_key)
                    client.download_fileobj(bucket, object_key, input_file)
                    input_file.seek(0)
                    reader = csv.reader(line.decode("utf-8-sig") for line in input_file)
                    try:
                        raw_header = next(reader)
                    except StopIteration as exc:
                        raise CsvMergeError(f"Input CSV is empty: {object_key}") from exc
                    actual_columns = _normalize_header(raw_header, object_key)
                    if actual_columns != expected_columns:
                        raise CsvMergeError(f"CSV header changed while merging: {object_key}")
                    positions = {column: index for index, column in enumerate(actual_columns)}
                    for row_number, row in enumerate(reader, start=2):
                        if len(row) > len(actual_columns):
                            raise CsvMergeError(f"Too many fields in {object_key} at row {row_number}")
                        writer.writerow([
                            row[positions[column]] if column in positions and positions[column] < len(row) else ""
                            for column in merged_schema
                        ])
                        source_rows += 1
                        rows_written += 1

                source_stats.append({
                    "object_key": object_key,
                    "source": _source_label(object_key),
                    "rows": source_rows,
                })
                logger.info("Merged %s rows from %s", source_rows, object_key)

            output_file.flush()
            logger.info("Uploading merged CSV to R2 as %s (%s rows)", output_key, rows_written)
            with output_path.open("rb") as upload_stream:
                client.upload_fileobj(upload_stream, bucket, output_key, ExtraArgs={"ContentType": "text/csv"})
        except Exception:
            logger.exception("CSV merge failed; output was not published")
            raise
        finally:
            # Close before unlinking so cleanup also works on Windows.
            output_file.close()
            output_path.unlink(missing_ok=True)

    if cleanup_sources:
        for object_key in object_keys:
            client.delete_object(Bucket=bucket, Key=object_key)
            logger.info("Deleted source object %s", object_key)

    return {
        "output_object_key": output_key,
        "rows_written": rows_written,
        "columns": list(merged_schema),
        "sources": source_stats,
    }
