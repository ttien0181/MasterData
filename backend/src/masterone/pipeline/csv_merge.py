"""Stream and merge CSV objects from Cloudflare R2 using their actual headers.

Stream Processing design
------------------------
Source files are read **directly from R2** via ``iter_lines()`` — no per-source
temp file is downloaded to disk.  Only the merged output is buffered in a local
``NamedTemporaryFile`` before being uploaded to R2.  This keeps peak memory usage
proportional to one output chunk, not the sum of all input files.

Merged Schema
-------------
When all incoming columns belong to the predefined BILL/CRM/ERP schema,
``KNOWN_MERGED_SCHEMA`` is used so the column order is deterministic and
business-meaningful (common columns first, then per-source columns in the order
BILL → CRM → ERP).  For any other column set the schema is built dynamically
(common columns first, extras appended in encounter order).
"""

import csv
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from masterone.pipeline.r2 import create_r2_client

logger = logging.getLogger(__name__)

# Maximum number of source files allowed in a single merge operation.
MAX_SOURCES = 10

# ---------------------------------------------------------------------------
# Predefined merged schema for BILL / CRM / ERP sources.
# Common columns first (shared by all three), then per-source business columns
# in the order BILL → CRM → ERP.  ``currency`` appears once (after BILL) even
# though ERP also carries it.  Total: 44 columns.
# ---------------------------------------------------------------------------
KNOWN_MERGED_SCHEMA: tuple[str, ...] = (
    # ── Common (all sources) ──────────────────────────────────────────────
    "record_id", "entity_id", "source", "name", "phone", "email",
    "address", "address_id", "noise_type", "difficulty", "temporal_case",
    # ── BILL-specific ─────────────────────────────────────────────────────
    "billing_account_id", "latest_invoice_number", "latest_invoice_date",
    "invoice_due_date", "invoice_status", "invoice_amount_vnd",
    "outstanding_amount_vnd", "last_payment_date", "payment_method",
    "invoice_delivery_channel", "currency",
    # ── CRM-specific ──────────────────────────────────────────────────────
    "crm_customer_id", "customer_status", "lifecycle_stage", "lead_source",
    "customer_segment", "preferred_contact_channel", "marketing_consent",
    "consent_updated_at", "assigned_sales_rep_id", "loyalty_tier",
    "last_contact_at",
    # ── ERP-specific (``currency`` already counted above) ─────────────────
    "erp_customer_code", "customer_group", "sales_organization",
    "distribution_channel", "division", "account_status", "payment_terms",
    "credit_limit_vnd", "customer_since", "order_count", "lifetime_sales_vnd",
)

_KNOWN_MERGED_SET: frozenset[str] = frozenset(KNOWN_MERGED_SCHEMA)


class CsvMergeError(ValueError):
    """Raised when an input CSV cannot be safely merged."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


def _build_index_map(
    actual_columns: tuple[str, ...],
    merged_schema: tuple[str, ...],
    object_key: str,
) -> dict[str, int]:
    """Return ``{column_name: source_position}`` and log the column mapping.

    Only columns that exist in *merged_schema* are included; extra columns in
    the source file are silently ignored.
    """
    positions = {col: idx for idx, col in enumerate(actual_columns)}
    present = [col for col in merged_schema if col in positions]
    absent  = [col for col in merged_schema if col not in positions]
    logger.info(
        "Column mapping for %s: %d present, %d absent (will be filled '')",
        object_key, len(present), len(absent),
    )
    if absent:
        logger.debug("  absent columns: %s", absent)
    return positions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_csv_columns(client, bucket: str, object_key: str) -> tuple[str, ...]:
    """Read and normalise only the CSV header from an R2 object (streaming)."""
    response = client.get_object(Bucket=bucket, Key=object_key)
    body = response["Body"]
    try:
        reader = csv.reader(line.decode("utf-8-sig") for line in body.iter_lines())
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise CsvMergeError(f"Input CSV is empty: {object_key}") from exc
        columns = _normalize_header(raw_header, object_key)
        logger.info("Read %d columns from %s", len(columns), object_key)
        return columns
    except UnicodeDecodeError as exc:
        raise CsvMergeError(f"CSV header is not valid UTF-8: {object_key}") from exc
    finally:
        body.close()


def build_merged_schema(source_columns: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Dynamic fallback: common columns first, then per-source extras."""
    if not source_columns:
        raise CsvMergeError("At least one source header is required")

    common = set(source_columns[0])
    for columns in source_columns[1:]:
        common.intersection_update(columns)

    ordered_common = [col for col in source_columns[0] if col in common]
    merged = list(ordered_common)
    seen = set(ordered_common)
    for columns in source_columns:
        for col in columns:
            if col not in seen:
                merged.append(col)
                seen.add(col)
    return tuple(merged)


def resolve_merged_schema(source_columns: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Choose KNOWN_MERGED_SCHEMA when all columns are recognised; otherwise
    fall back to the dynamic schema builder."""
    all_cols: set[str] = set()
    for cols in source_columns:
        all_cols.update(cols)

    unknown = all_cols - _KNOWN_MERGED_SET
    if not unknown:
        logger.info(
            "All source columns match KNOWN_MERGED_SCHEMA — using canonical "
            "%d-column order.", len(KNOWN_MERGED_SCHEMA),
        )
        return KNOWN_MERGED_SCHEMA

    logger.warning(
        "Unknown columns %s — falling back to dynamic merged schema.",
        sorted(unknown),
    )
    return build_merged_schema(source_columns)


def _source_label(object_key: str) -> str:
    return Path(object_key).name


def merge_csv_objects(
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    object_keys: list[str],
    cleanup_sources: bool = True,
    output_key: str | None = None,
) -> dict[str, object]:
    """Merge R2 CSV objects into one file using true stream processing.

    Each source file is read **directly from R2** via ``iter_lines()`` —
    no per-source temp file is written to local disk.  Peak disk usage is
    bounded by the size of the merged output file alone.

    Args:
        endpoint_url: Cloudflare R2 (S3-compatible) endpoint.
        access_key_id: R2 access key.
        secret_access_key: R2 secret key.
        bucket: Target bucket name.
        object_keys: List of R2 object keys to merge (must be under
            ``incoming/``).  1 – MAX_SOURCES files supported.
        cleanup_sources: Delete source objects from R2 after a successful
            merge (default ``True``).
        output_key: Destination key for the merged CSV.  Defaults to
            ``merged/<uuid>.csv``.

    Returns:
        A dict with ``output_object_key``, ``rows_written``, ``columns``,
        and ``sources`` statistics.
    """
    # ── Validation ─────────────────────────────────────────────────────────
    if not object_keys:
        raise CsvMergeError("At least one source file is required")
    if len(object_keys) > MAX_SOURCES:
        raise CsvMergeError(f"At most {MAX_SOURCES} source files are supported")
    if len(set(object_keys)) != len(object_keys):
        raise CsvMergeError("Duplicate object keys are not allowed")
    if any(not key.startswith("incoming/") or key.endswith("/") for key in object_keys):
        raise CsvMergeError("Only objects under incoming/ can be merged")

    client = create_r2_client(endpoint_url, access_key_id, secret_access_key)

    # ── Step 1: Read headers only (one streaming call per file) ────────────
    logger.info("Reading headers from %d source file(s)…", len(object_keys))
    source_columns = [get_csv_columns(client, bucket, key) for key in object_keys]
    merged_schema = resolve_merged_schema(source_columns)

    output_key = output_key or f"merged/{uuid4()}.csv"
    if output_key.endswith("/") or output_key.startswith("incoming/"):
        raise CsvMergeError("Output object key must be a file outside incoming/")

    rows_written = 0
    source_stats: list[dict[str, object]] = []

    # ── Step 2: Stream each source → write aligned rows to output temp ─────
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", suffix=".csv", delete=False
    ) as output_file:
        output_path = Path(output_file.name)
        try:
            writer = csv.writer(output_file, lineterminator="\n")
            writer.writerow(merged_schema)

            for object_key, expected_columns in zip(object_keys, source_columns, strict=True):
                source_rows = 0
                logger.info("Streaming %s directly from R2…", object_key)

                # Open a streaming connection — no local download.
                response = client.get_object(Bucket=bucket, Key=object_key)
                body = response["Body"]
                try:
                    reader = csv.reader(
                        line.decode("utf-8-sig") for line in body.iter_lines()
                    )
                    # Consume and validate header.
                    try:
                        raw_header = next(reader)
                    except StopIteration as exc:
                        raise CsvMergeError(f"Input CSV is empty: {object_key}") from exc

                    actual_columns = _normalize_header(raw_header, object_key)
                    if actual_columns != expected_columns:
                        raise CsvMergeError(
                            f"CSV header changed between header-read and merge: {object_key}"
                        )

                    # Build and log column index mapping.
                    positions = _build_index_map(actual_columns, merged_schema, object_key)

                    # Stream rows, align to merged schema, write immediately.
                    for row_number, row in enumerate(reader, start=2):
                        if len(row) > len(actual_columns):
                            raise CsvMergeError(
                                f"Too many fields in {object_key} at row {row_number}"
                            )
                        writer.writerow([
                            row[positions[col]]
                            if col in positions and positions[col] < len(row)
                            else ""
                            for col in merged_schema
                        ])
                        source_rows += 1
                        rows_written += 1

                        if source_rows % 10_000 == 0:
                            logger.info(
                                "  %s: %d rows processed…", object_key, source_rows
                            )
                finally:
                    body.close()

                source_stats.append({
                    "object_key": object_key,
                    "source": _source_label(object_key),
                    "rows": source_rows,
                })
                logger.info(
                    "Merged %d rows from %s (total so far: %d)",
                    source_rows, object_key, rows_written,
                )

            output_file.flush()
            logger.info(
                "Uploading merged CSV → R2:%s  (%d rows, %d columns)",
                output_key, rows_written, len(merged_schema),
            )
            with output_path.open("rb") as upload_stream:
                client.upload_fileobj(
                    upload_stream, bucket, output_key,
                    ExtraArgs={"ContentType": "text/csv"},
                )

        except Exception:
            logger.exception("CSV merge failed; output was not published to R2")
            raise
        finally:
            # Always remove local temp file — even on Windows where the file
            # must be closed before unlinking.
            output_file.close()
            output_path.unlink(missing_ok=True)

    # ── Step 3: Cleanup source objects ─────────────────────────────────────
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
