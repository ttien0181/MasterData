from unicodedata import normalize


SOURCE_PRIORITY = {"ERP": 0, "CRM": 1, "BILL": 2}
FIELDS = ("name", "phone", "email", "address")


def build_golden(member_ids: list[str], records_by_id: dict[str, dict]) -> dict:
    ordered = sorted(member_ids, key=lambda record_id: (
        SOURCE_PRIORITY.get(records_by_id[record_id]["source_system"], 99),
        records_by_id[record_id]["external_id"],
    ))
    fields = {}
    for field in FIELDS:
        for record_id in ordered:
            value = records_by_id[record_id]["raw_fields"].get(field, "").strip()
            if value:
                display = normalize("NFC", " ".join(value.split())) if field == "name" else value
                fields[field] = {"value": display, "source_record_id": record_id,
                                 "source_system": records_by_id[record_id]["source_system"]}
                break
    return {"member_record_ids": sorted(member_ids), "fields": fields}
