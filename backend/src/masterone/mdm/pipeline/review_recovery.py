"""Versioned support for manual review; never changes the strong-anchor floor."""
import re

VERSION = 'person-review-support-v1'
_EXACT_NAMES = {'EX', 'NX', 'NA'}
_GOOD_NAMES = _EXACT_NAMES | {'HS'}
_ADMIN = {'phuong', 'xa', 'quan', 'huyen', 'tinh', 'ward', 'district', 'province', 'city', 'commune'}


def address_key(address):
    """A numbered street, excluding city/ward-only and unnumbered addresses."""
    from masterone.mdm.pipeline.standardization import fold_text
    if not isinstance(address, str):
        return ''
    first = fold_text(address).split(',')[0].strip()
    match = re.fullmatch(r'(?:so\s+)?(\d+[a-z]?(?:[/\-]\d+[a-z]?)*)(?:\s+)(.+)', first)
    if not match:
        return ''
    house, street = match.groups()
    words = street.split()
    if not words or words[0] in _ADMIN:
        return ''
    if words[0] in {'duong', 'street', 'road'}:
        words = words[1:]
    if not words or words[0] in _ADMIN:
        return ''
    if len([w for w in words if any(c.isalpha() for c in w)]) < 2 or len(''.join(words)) < 6:
        return ''
    return house + ' ' + ' '.join(words)


def _safe_contacts(record, field, frequencies):
    from masterone.mdm.pipeline.matching import _contact_items, SHARED_CONTACT_MIN_FREQ
    nf = record['normalized_fields']
    raw = record.get('raw_fields', {})
    risk_keys = (f'_{field}_shared', f'_{field}_shared_risk', f'{field}_shared', f'{field}_shared_risk')
    if any(nf.get(key) is True or str(raw.get(key, '')).strip().lower() in {'true', '1'} for key in risk_keys):
        return ()
    items = _contact_items(record, field)
    if len(items) > 32:
        return ()
    return tuple(sorted({item['value'] for item in items if item.get('value')
        and item.get('quality', 'VALID') == 'VALID'
        and not any(item.get(key) for key in ('shared','shared_risk','historical','valid_to','origin_id'))
        and frequencies.get(field, {}).get(item['value'], 0) <= SHARED_CONTACT_MIN_FREQ}))


def _one_edit(a, b):
    if len(a) != len(b) or a == b:
        return False
    different = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    return len(different) == 1 or (len(different) == 2 and different[1] == different[0] + 1
        and a[different[0]] == b[different[1]] and a[different[1]] == b[different[0]])


def support(left, right, prepared, frequencies=None):
    """Return corroborating fields, or None. Inputs are never mutated."""
    from masterone.mdm.pipeline.decision_semantics import person_constraints
    from masterone.mdm.pipeline.matching import value_frequencies
    from masterone.mdm.pipeline.pair_grouping import unverified_identifier_relation
    if person_constraints(left, right).forbids_merge:
        return None
    a, b = left['normalized_fields'], right['normalized_fields']
    if (unverified_identifier_relation(left, right) == 'DIFFERENT_VALUE'
            and a.get('government_id_namespace','') == b.get('government_id_namespace','')):
        return None
    states = {f: value.get('state') for f, value in prepared.get('comparisons', {}).items()}
    if any(nf.get('_field_quality', {}).get('name') != 'VALID' or not nf.get('name') for nf in (a, b)):
        return None
    names = states.get('name')
    full_names = all(len(nf.get('name', '').split()) >= 2 for nf in (a, b))
    from masterone.mdm.pipeline.standardization import parse_dob
    dates = [parse_dob(nf.get('dob', '')) for nf in (a, b)]
    complete_dob = states.get('dob') == 'EX' and dates[0]['canonical'] == dates[1]['canonical'] and all(
        date['quality'] == 'VALID' and date['day'] and date['month'] for date in dates) and all(
        nf.get('_field_quality', {}).get('dob') == 'VALID' for nf in (a, b))
    detail = address_key(a.get('address'))
    same_street = bool(detail and detail == address_key(b.get('address'))
                       and states.get('address') in {'EX', 'NX', 'HS'})

    def evidence(kind, fields):
        return {'policy_version': VERSION, 'kind': kind, 'fields': fields}

    if full_names and names in _EXACT_NAMES and complete_dob:
        return evidence('NAME_FULL_DOB', ['name', 'dob'])
    if full_names and complete_dob and same_street:
        return evidence('FULL_DOB_DETAIL_ADDRESS', ['dob', 'address'])
    ids = (a.get('government_id'), b.get('government_id'))
    kind, namespace = a.get('government_id_type'), a.get('government_id_namespace')
    person_identifier = bool(namespace and ((namespace,kind) in {('VN','CCCD_12'),('VN','CMND_9')}
        or all(nf.get('government_id_identity_unique') is True for nf in (a,b))))
    if (full_names and names in _GOOD_NAMES and ids[0] and ids[0] == ids[1] and len(set(ids[0])) >= 3
            and all(nf.get('_field_quality', {}).get('government_id') == 'VALID' for nf in (a, b))
            and a.get('government_id_type') == b.get('government_id_type')
            and a.get('government_id_type') not in {None, '', 'UNKNOWN'}
            and person_identifier and namespace == b.get('government_id_namespace')):
        return evidence('NAME_UNVERIFIED_ID', ['name', 'government_id'])
    if not full_names or names not in _GOOD_NAMES or states.get('dob') == 'CF' or not (complete_dob or same_street):
        return None
    frequencies = frequencies if frequencies is not None else value_frequencies([left, right])
    for field in ('phone', 'email'):
        va, vb = _safe_contacts(left, field, frequencies), _safe_contacts(right, field, frequencies)
        for x in va:
            for y in vb:
                if field == 'phone' and len(x) == 10 and x.isdigit() and y.isdigit() and _one_edit(x, y):
                    return evidence('NAME_NEAR_CONTACT_DOB' if complete_dob else 'NAME_NEAR_CONTACT_ADDRESS',
                                    ['name', field, 'dob' if complete_dob else 'address'])
                if field == 'email' and '@' in x and '@' in y:
                    lx, dx = x.rsplit('@', 1)
                    ly, dy = y.rsplit('@', 1)
                    if dx == dy and min(len(lx), len(ly)) >= 6 and _one_edit(lx, ly):
                        return evidence('NAME_NEAR_CONTACT_DOB' if complete_dob else 'NAME_NEAR_CONTACT_ADDRESS',
                                        ['name', field, 'dob' if complete_dob else 'address'])
    return None
