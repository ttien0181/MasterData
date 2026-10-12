"""Versioned review assessment. Historical matcher/profile/decisions remain intact."""
from copy import deepcopy

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.decision_semantics import evaluate_prepared, person_constraints
from masterone.mdm.pipeline.matching import compare_and_score_rule
from masterone.mdm.pipeline.standardization import preserve_verification

VERSION = 'person-review-assessment-v1'


def assessment_profile(profile=None):
    return {**(profile or {}), 'decision_semantics_v2_enabled': True,
            'cluster_safety_v2_enabled': True, 'generation_staging_enabled': False}


def assessment_record(record):
    item = deepcopy(record)
    preserve_verification(item.get('raw_fields', {}), item['normalized_fields'])
    return item


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


def classify(result, identifier_relation):
    if result['decision'] in {'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'}:
        return 'BLOCKED', result['reason']
    states = {entry['field']: entry['state'] for entry in result.get('field_evidence', [])}
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


def assess_pair(left, right, frequencies=None, negative_pairs=frozenset()):
    left, right = assessment_record(left), assessment_record(right)
    with engine_context(assessment_profile()):
        prepared = compare_and_score_rule(left, right, set(), frequencies, negative_pairs)
        result, _, _ = evaluate_prepared(prepared, left, right, negative_pairs=negative_pairs)
        constraints = person_constraints(left, right, negative_pairs)
    relation = unverified_identifier_relation(left, right)
    group, subgroup = classify(result, relation)
    rule_conclusion = result['decision']
    # New assessment is human review assistance, including clear patterns.
    if result['decision'] == 'MERGEABLE':
        result = {**result, 'decision': 'REVIEW', 'triage_status': 'REVIEW', 'reason': 'CLEAR_PATTERN_REVIEW'}
    # A soft contradiction can never inherit automatic eligibility from a broad A rule.
    if group == 'D' and result['decision'] not in {'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'}:
        result = {**result, 'decision': 'REVIEW', 'triage_status': 'REVIEW',
                  'reason': 'UNVERIFIED_ID_CONFLICT' if relation == 'DIFFERENT_VALUE' else 'NON_HARD_CONFLICT'}
    return {**result, 'left_id': left['id'], 'right_id': right['id'],
            'primary_group': group, 'subgroup': subgroup, 'assessment_version': VERSION, 'rule_conclusion': rule_conclusion,
            'constraint_codes': list(constraints.violations) + (['STEWARD_NEGATIVE_DECISION'] if constraints.cannot_link else []),
            'unverified_identifier_relation': relation}
