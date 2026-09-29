from collections import defaultdict

from masterone.mdm.models import Candidate, SourceRecord


class PrototypeMatcher:
    engine_id = "exact-signal-prototype-v1"
    fields = ("email", "phone", "tax_id", "name")

    def _candidates(self, pairs: dict) -> list[Candidate]:
        return [
            Candidate(
                import_id=left.import_id,
                left_record_id=left.id,
                right_record_id=right.id,
                engine_id=self.engine_id,
                evidence=evidence,
            )
            for left, right, evidence in pairs.values()
        ]

    def match(self, records: list[SourceRecord]) -> list[Candidate]:
        buckets: dict[tuple[str, str], list[SourceRecord]] = defaultdict(list)
        pairs: dict[tuple[str, str], tuple[SourceRecord, SourceRecord, list[dict]]] = {}
        for record in records:
            for field in self.fields:
                value = record.normalized_fields.get(field, "").strip().casefold()
                if value:
                    for other in buckets[(field, value)]:
                        if other.source == record.source:
                            continue
                        left, right = sorted((other, record), key=lambda item: item.id)
                        pair = (left.id, right.id)
                        entry = pairs.setdefault(pair, (left, right, []))
                        signal = {"field": field, "value": value}
                        if signal not in entry[2]:
                            entry[2].append(signal)
                        if len(pairs) >= 1000:
                            return self._candidates(pairs)
                    buckets[(field, value)].append(record)
        return self._candidates(pairs)
