"""Current person pair groups, shared by scoring, assessment and review fallback."""

VERSION = 'person-pair-classification-v2'
BLOCKED_DECISIONS = frozenset({'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'})


def unverified_identifier_relation(left, right):
    a, b = left['normalized_fields'], right['normalized_fields']
    if (not a.get('government_id') or not b.get('government_id') or
            a.get('_field_quality', {}).get('government_id') != 'VALID' or
            b.get('_field_quality', {}).get('government_id') != 'VALID' or
            a.get('government_id_type', 'UNKNOWN') == 'UNKNOWN' or
            a.get('government_id_type') != b.get('government_id_type')):
        return 'NOT_COMPARABLE'
    if a.get('_government_id_verified') and b.get('_government_id_verified'):
        return 'VERIFIED_METADATA'
    return 'SAME_VALUE' if a['government_id'] == b['government_id'] else 'DIFFERENT_VALUE'


def classify_states(states, identifier_relation='NOT_COMPARABLE'):
    conflicts = [field for field, state in states.items() if state == 'CF']
    if identifier_relation == 'DIFFERENT_VALUE':
        conflicts.append('unverified_government_id')
    if conflicts:
        kinds = {'name': 'D2_NAME_CONFLICT', 'phone': 'D1_NAME_PHONE_CONFLICT',
                 'email': 'D3_IDENTITY_CONFLICT', 'government_id': 'D3_IDENTITY_CONFLICT',
                 'dob': 'D4_DOB_CONFLICT', 'unverified_government_id': 'D_UNVERIFIED_ID_CONFLICT'}
        return 'D', kinds.get(conflicts[0], 'D_MULTI') if len(conflicts) == 1 else 'D_MULTI'
    good = states.get('name') in {'EX', 'NX', 'NA', 'HS'}
    phone, email = (states.get(field) in {'EX', 'NX'} for field in ('phone', 'email'))
    dob = states.get('dob') in {'EX', 'CL'}
    gov = states.get('government_id') == 'EX'
    if phone and dob and states.get('name') in {'EX', 'NX', 'NA', 'HS', 'MS'}:
        return 'A', 'A3_NAME_PHONE_DOB'
    if good and gov:
        return 'A', 'A3_NAME_GOV_ID'
    if good and phone:
        return 'A', 'A1_NAME_PHONE'
    if good and email:
        return 'A', 'A2_NAME_EMAIL'
    if email and dob:
        return 'C', 'C1_EMAIL_DOB'
    if gov:
        return 'C', 'C3_GOV_ID_ONLY'
    if phone or email:
        return 'C', 'C_MULTI' if phone and email else 'C_NAME_ANCHOR_MISSING'
    if all(state in {'MI', 'IV', 'NC'} for state in states.values()):
        return 'C', 'C_INSUFFICIENT_DATA'
    return 'C', 'C_NO_VALID_IDENTITY_ANCHOR'


def classify_result(result, identifier_relation=None):
    if result.get('decision') in BLOCKED_DECISIONS:
        return 'BLOCKED', result.get('reason', 'HARD_CONFLICT')
    states = {entry['field']: entry['state'] for entry in result.get('field_evidence', [])}
    if not states:
        states = {field: value['state'] for field, value in result.get('comparisons', {}).items()}
    return classify_states(states, identifier_relation or result.get('unverified_identifier_relation', 'NOT_COMPARABLE'))
