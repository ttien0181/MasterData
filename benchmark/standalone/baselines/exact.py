"""B0 deterministic exact/rule baseline, independent of application code."""
from __future__ import annotations

import re
import unicodedata

from standalone.contracts.core import Decision, Record, Similarity


def _text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _digits(value: str | None) -> str:
    return "".join(re.findall(r"\d", value or ""))


class ExactRuleBaseline:
    """AUTO_MATCH on exact email, credible phone, or exact name plus address."""
    name = "exact"
    version = "B0-exact-rule-v1"

    def score(self, left: Record, right: Record) -> tuple[Similarity, tuple[str, ...]]:
        evidence: list[str] = []
        email_left, email_right = _text(left.fields.get("email")), _text(right.fields.get("email"))
        phone_left, phone_right = _digits(left.fields.get("phone")), _digits(right.fields.get("phone"))
        name_left, name_right = _text(left.fields.get("name")), _text(right.fields.get("name"))
        address_left, address_right = _text(left.fields.get("address")), _text(right.fields.get("address"))
        if email_left and email_left == email_right:
            evidence.append("email_exact")
        if len(phone_left) >= 7 and phone_left == phone_right:
            evidence.append("phone_exact_digits_ge_7")
        if name_left and address_left and name_left == name_right and address_left == address_right:
            evidence.extend(("name_exact", "address_exact"))
        score = Similarity(1.0 if evidence else 0.0)
        return score, tuple(evidence)

    @staticmethod
    def route(score: Similarity, evidence: tuple[str, ...]) -> Decision:
        return Decision.AUTO_MATCH if evidence else Decision.NO_MATCH
