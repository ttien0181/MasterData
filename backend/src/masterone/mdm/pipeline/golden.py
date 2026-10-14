"""Versioned survivorship with a legacy-shaped output adapter.

New drafts share eligibility, verification, quality, source and actual source time.
Previously stored golden records remain immutable and are read in their old shape.
"""

SOURCE_PRIORITY = {"CRM": 0, "ERP": 1, "BILL": 2}
FIELDS = ("name", "address", "dob", "government_id")
MULTI_VALUE_FIELDS = ("phone", "email")


def build_golden(member_ids, records_by_id, *, existing_golden=None, policy=None):
    from masterone.mdm.pipeline.cluster_policy import enabled
    from masterone.mdm.pipeline.golden_v2 import build_golden_v2

    ids = [rid for rid in member_ids if rid in records_by_id]
    result = build_golden_v2(ids, records_by_id, existing_golden=existing_golden, policy=policy)
    if enabled():
        return result
    fields = {}
    for field, data in result['fields'].items():
        if data.get('value') is None:
            continue
        fields[field] = {key: data[key] for key in
                         ('value', 'source_record_id', 'source_system', 'quality', 'provenance') if key in data}
        if field in MULTI_VALUE_FIELDS:
            fields[field]['values'] = [{key: item[key] for key in
                                       ('value', 'source_record_id', 'source_system', 'quality') if key in item}
                                      for item in data.get('values', [])]
    legacy = {'member_record_ids': sorted(member_ids), 'fields': fields}
    if '_golden_override' in result:
        legacy['_golden_override'] = result['_golden_override']
    return legacy
