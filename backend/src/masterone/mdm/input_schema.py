"""Versioned P-119 source contract; adapted from OneMaster source_schema_v4.

Original column names and values are retained. Reports contain schema metadata,
never input row values. No runtime dependency on the reference repository.
"""

import json
import unicodedata
from dataclasses import dataclass
from importlib.resources import files

from fastapi import HTTPException

RESOLVER_VERSION = "p119_source_schema_v1"
CONTRACT_VERSION = "p119_input_v1"


def load_contract(version=CONTRACT_VERSION):
    if version != CONTRACT_VERSION:
        raise ValueError("Unsupported input contract version")
    return json.loads(files("masterone.mdm").joinpath("input_contract_v1.json").read_text(encoding="utf-8"))


def column_key(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


class InputMappingError(HTTPException):
    def __init__(self, code, message, **details):
        super().__init__(422, message)
        self.report = {"code": code, "message": message,
                       "resolver_version": RESOLVER_VERSION, "details": details}


def input_error(code, message, **details):
    raise InputMappingError(code, message, **details)


@dataclass(frozen=True)
class SourceMapping:
    fields: dict
    unknown_fields: list
    contract_version: str
    contract_sha256: str
    required_fields: tuple
    allowed_sources: tuple

    def provenance(self):
        return {"contract_version": self.contract_version, "contract_sha256": self.contract_sha256,
                "resolver_version": RESOLVER_VERSION, "fields": dict(self.fields),
                "unknown_fields": list(self.unknown_fields)}


def resolve_source_schema(header, contract=None):
    import hashlib

    contract = load_contract() if contract is None else contract
    keys = [column_key(c) for c in (header or [])]
    if not keys or any(not key for key in keys):
        input_error("INVALID_INPUT_SCHEMA", "CSV có header rỗng")
    if len(set(header)) != len(header):
        input_error("DUPLICATE_HEADER", "CSV có header trùng")
    if len(set(keys)) != len(keys):
        input_error("UNICODE_HEADER_COLLISION", "Header CSV trùng sau chuẩn hóa Unicode")
    if any(key.startswith("raw__") for key in keys):
        input_error("INVALID_INPUT_SCHEMA", "Prefix raw__ được dành cho raw values")
    index = dict(zip(keys, header))
    mapping = {}
    claimed = {}
    for field, aliases in contract["fields"].items():
        matches = list(dict.fromkeys(index[column_key(alias)] for alias in aliases if column_key(alias) in index))
        # Explicit original source ID wins over auxiliary snapshot IDs.
        if field == "source_record_id" and "source_record_id" in index:
            matches = [index["source_record_id"]]
        if len(matches) > 1:
            input_error("AMBIGUOUS_INPUT_SCHEMA", "Nhiều cột cùng ánh xạ một trường",
                        ambiguous_fields=[field], columns=matches)
        if matches:
            column = matches[0]
            if column in claimed:
                input_error("AMBIGUOUS_INPUT_SCHEMA", "Một cột ánh xạ nhiều trường",
                            ambiguous_fields=[claimed[column], field], columns=[column])
            mapping[field] = column
            claimed[column] = field
    missing = [field for field in contract["required_fields"] if field not in mapping]
    if missing:
        input_error("INVALID_INPUT_SCHEMA", "Thiếu trường bắt buộc", missing_fields=missing,
                    supported_aliases={field: contract["fields"][field] for field in missing})
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return SourceMapping(mapping, [c for c in header if c not in claimed], contract["version"], digest,
                         tuple(contract["required_fields"]), tuple(contract["allowed_sources"]))
