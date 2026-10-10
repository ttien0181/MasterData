"""Deterministic, provenance-preserving Vietnamese-oriented normalization."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from standalone.contracts.core import FEATURE_ALLOWLIST, Record, RecordFeatures
from standalone.contracts.interfaces import NormalizationResult
from standalone.references.local import LocalReferenceProvider, load_local_references


def normalize_unicode(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return unicodedata.normalize("NFC", unicodedata.normalize("NFKC", value)).strip()


def normalize_text(value: str | None) -> str | None:
    value = normalize_unicode(value)
    if value is None:
        return None
    return " ".join(value.casefold().split()) or None


def accentless_vietnamese(value: str | None) -> str | None:
    value = normalize_text(value)
    if value is None:
        return None
    decomposed = unicodedata.normalize("NFD", value)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", without_marks.replace("đ", "d").replace("Đ", "D"))


@dataclass(frozen=True)
class NormalizedValue:
    raw: str | None
    normalized: str | None
    accentless: str | None = None
    provenance: str = "raw"


@dataclass(frozen=True)
class NormalizedRecord:
    source: str
    fields: Mapping[str, NormalizedValue]
    quality_flags: tuple[str, ...]
    reference_version: str | None = None
    address_reference_evidence: AddressEvidence | None = None
    use_accentless_name: bool = False

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        unknown = set(self.fields) - FEATURE_ALLOWLIST
        if unknown:
            raise ValueError(f"normalized record has unsupported fields: {sorted(unknown)}")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "quality_flags", tuple(sorted(set(self.quality_flags))))

    def feature_values(self, *, use_accentless_name: bool | None = None) -> dict[str, str | None]:
        if use_accentless_name is None:
            use_accentless_name = self.use_accentless_name
        result = {key: value.normalized for key, value in self.fields.items()}
        if use_accentless_name and "name" in self.fields:
            result["name"] = self.fields["name"].accentless
        return result


@dataclass(frozen=True)
class AddressEvidence:
    components: tuple[str, ...]
    unit_candidates: tuple[str, ...]
    mapping_status: str
    reference_version: str | None
    quality_flags: tuple[str, ...]


def _phone(value: str | None) -> tuple[str | None, tuple[str, ...]]:
    normalized = normalize_unicode(value)
    if normalized is None:
        return None, ("phone_missing",)
    compact = re.sub(r"[\s().-]", "", normalized)
    had_plus84 = compact.startswith("+84")
    had_0084 = compact.startswith("0084")
    digits = "".join(ch for ch in compact if ch.isdigit())
    if had_plus84 and digits.startswith("84"):
        digits = "0" + digits[2:]
    elif had_0084 and digits.startswith("0084"):
        digits = "0" + digits[4:]
    # Current Vietnamese domestic numbering is validated conservatively. Invalid
    # values are excluded from comparison; no digits are fabricated or padded.
    if not (digits.startswith("0") and len(digits) in {10, 11}):
        return None, ("phone_invalid",)
    return digits, ()


def _email(value: str | None) -> tuple[str | None, tuple[str, ...]]:
    normalized = normalize_unicode(value)
    if normalized is None:
        return None, ("email_missing",)
    if normalized.count("@") != 1:
        return None, ("email_invalid",)
    local, domain = normalized.rsplit("@", 1)
    if (not local or not domain or any(ch.isspace() for ch in normalized)
            or domain.startswith(".") or domain.endswith(".") or ".." in domain):
        return None, ("email_invalid",)
    try:
        domain_ascii = domain.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return None, ("email_invalid",)
    # Preserve local-part semantics except for conservative Unicode/case folding;
    # plus tags and provider-specific dots are not rewritten.
    return f"{local.casefold()}@{domain_ascii}", ()


def address_evidence(value: str | None, provider: LocalReferenceProvider | None = None,
                     *, admin_version: str | None = None) -> AddressEvidence:
    normalized = normalize_text(value)
    if normalized is None:
        return AddressEvidence((), (), "missing", provider.version if provider else None, ("address_missing",))
    components = tuple(part.strip() for part in normalized.split(",") if part.strip())
    if provider is None:
        return AddressEvidence(components, (), "unresolved", None, ("address_reference_unavailable",))
    candidates = {}
    for part in components:
        for candidate in provider.resolve_alias(part, admin_version):
            candidates[(candidate.admin_version, candidate.unit_id)] = candidate
    units = tuple(f"{version or ''}:{unit_id}" for version, unit_id in sorted(candidates))
    status = "ambiguous" if len(units) > 1 else "resolved" if units else "unresolved"
    flags = ("address_mapping_ambiguous",) if status == "ambiguous" else ()
    return AddressEvidence(components, units, status, provider.version, flags)


class VietnameseNormalizer:
    version = "vn-normalizer-v1.0"

    def __init__(self, *, use_accentless_name: bool = True,
                 reference_provider: LocalReferenceProvider | None = None,
                 admin_version: str | None = None) -> None:
        self.use_accentless_name = use_accentless_name
        if reference_provider is None:
            try:
                _, reference_provider = load_local_references()
            except FileNotFoundError:
                reference_provider = None
        self.reference_provider = reference_provider
        self.admin_version = admin_version

    def transform(self, record: Record | RecordFeatures, source: str | None = None
                  ) -> NormalizedRecord | NormalizationResult:
        is_raw_record = isinstance(record, Record)
        if is_raw_record:
            source = record.source
        elif not source:
            raise ValueError("source is required when normalizing RecordFeatures")
        raw_fields = record.fields
        flags: list[str] = []
        fields: dict[str, NormalizedValue] = {}
        name = normalize_text(raw_fields.get("name"))
        fields["name"] = NormalizedValue(raw_fields.get("name"), name,
                                         accentless_vietnamese(name), "unicode-nfc-casefold-space-v1")
        flags.extend(("name_missing",) if name is None else ())
        phone, phone_flags = _phone(raw_fields.get("phone"))
        fields["phone"] = NormalizedValue(raw_fields.get("phone"), phone, None, "vn-phone-conservative-v1")
        flags.extend(phone_flags)
        email, email_flags = _email(raw_fields.get("email"))
        fields["email"] = NormalizedValue(raw_fields.get("email"), email, None, "email-parse-v1")
        flags.extend(email_flags)
        address = normalize_text(raw_fields.get("address"))
        fields["address"] = NormalizedValue(raw_fields.get("address"), address, None,
                                             "unicode-nfc-casefold-space-v1")
        address_info = address_evidence(raw_fields.get("address"), self.reference_provider,
                                         admin_version=self.admin_version)
        flags.extend(address_info.quality_flags)
        if address is not None and address_info.mapping_status == "unresolved":
            flags.append("address_mapping_unresolved")
        normalized = NormalizedRecord(
            source, fields, tuple(flags),
            self.reference_provider.version if self.reference_provider else None,
            address_info, self.use_accentless_name,
        )
        if is_raw_record:
            return normalized
        return NormalizationResult(
            RecordFeatures({key: (fields[key].accentless if key == "name" and self.use_accentless_name
                                  else value.normalized) for key, value in fields.items()}),
            {key: value.provenance for key, value in fields.items()}, tuple(flags),
        )


def address_compatibility(left: AddressEvidence, right: AddressEvidence) -> str:
    """Reference overlap is supporting evidence; ambiguity is never collapsed."""
    if left.mapping_status == "missing" or right.mapping_status == "missing":
        return "unknown"
    if left.mapping_status == "ambiguous" or right.mapping_status == "ambiguous":
        return "ambiguous"
    if not left.unit_candidates or not right.unit_candidates:
        return "unknown"
    return "compatible" if set(left.unit_candidates) & set(right.unit_candidates) else "different_reference_units"
