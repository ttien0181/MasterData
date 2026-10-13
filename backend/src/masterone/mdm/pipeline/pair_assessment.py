"""Versioned review assessment. Historical matcher/profile/decisions remain intact."""
from copy import deepcopy

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.decision_semantics import evaluate_prepared
from masterone.mdm.pipeline.matching import compare_and_score_rule
from masterone.mdm.pipeline.pair_grouping import classify_result, unverified_identifier_relation
from masterone.mdm.pipeline.standardization import preserve_verification

VERSION = 'person-review-assessment-v2'


def assessment_profile(profile=None):
    return {**(profile or {}), 'decision_semantics_v2_enabled': True,
            'cluster_safety_v2_enabled': True, 'generation_staging_enabled': False}


def assessment_record(record):
    item = deepcopy(record)
    preserve_verification(item.get('raw_fields', {}), item['normalized_fields'])
    return item


def classify(result, identifier_relation):
    return classify_result(result, identifier_relation)


def assess_pair(left, right, frequencies=None, negative_pairs=frozenset()):
    left, right = assessment_record(left), assessment_record(right)
    with engine_context(assessment_profile()):
        return assess_prepared_pair(left, right, frequencies, negative_pairs)


def assess_prepared_pair(left, right, frequencies=None, negative_pairs=frozenset()):
    """Assess read-only records already prepared under assessment_profile()."""
    prepared = compare_and_score_rule(left, right, set(), frequencies, negative_pairs,
                                      defer_hard_constraints=True)
    result, evaluation, _ = evaluate_prepared(prepared, left, right, negative_pairs=negative_pairs)
    constraints = evaluation.constraints
    relation = unverified_identifier_relation(left, right)
    group, subgroup = classify(result, relation)
    rule_conclusion = result['decision']
    # Preserve the conclusion, while every clear pattern still requires review.
    if result['decision'] == 'MERGEABLE':
        result = {**result, 'triage_status': 'REVIEW', 'triage_reason': 'CLEAR_PATTERN_REVIEW',
                  'reason': 'CLEAR_PATTERN_REVIEW'}
    # A soft contradiction can never inherit automatic eligibility from a broad A rule.
    if group == 'D' and result['decision'] not in {'NOT_MERGEABLE', 'BLOCKED', 'NON_MATCH'}:
        result = {**result, 'decision': 'REVIEW', 'triage_status': 'REVIEW',
                  'reason': 'UNVERIFIED_ID_CONFLICT' if relation == 'DIFFERENT_VALUE' else 'NON_HARD_CONFLICT'}
    return {**result, 'left_id': left['id'], 'right_id': right['id'],
            'primary_group': group, 'subgroup': subgroup, 'assessment_version': VERSION, 'rule_conclusion': rule_conclusion,
            'constraint_codes': list(constraints.violations) + (['STEWARD_NEGATIVE_DECISION'] if constraints.cannot_link else []),
            'unverified_identifier_relation': relation}
