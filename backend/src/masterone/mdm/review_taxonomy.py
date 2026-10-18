"""Action-oriented presentation metadata, independent of matcher eligibility.

This module never changes operative groups, decisions, profile hashes, or scores.
Group A means proposed match, B needs comparison, C insufficient evidence (review
is optional). These labels do not imply a validated match probability.
"""
from collections.abc import Mapping

from sqlalchemy import and_, case, false, func, or_

_BLOCKED = ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH')
_FIELDS = frozenset(('name', 'phone', 'email', 'address', 'dob', 'government_id'))
_STATES = frozenset(('EX', 'NX', 'NA', 'HS', 'MS', 'WS', 'CF', 'MI', 'IV', 'NC', 'CL'))
_RELATIONS = frozenset(('NOT_COMPARABLE', 'VERIFIED_METADATA', 'SAME_VALUE', 'DIFFERENT_VALUE'))
_SUPPORT = frozenset(('NAME_FULL_DOB', 'FULL_DOB_DETAIL_ADDRESS', 'NAME_UNVERIFIED_ID',
                      'NAME_NEAR_CONTACT_DOB', 'NAME_NEAR_CONTACT_ADDRESS'))


def _routes(group, subgroup, decision, triage, reason, disagreement, *, sql=False):
    """One ordered rule table for Python and SQL; callers normalize null strings."""
    either = or_ if sql else lambda *items: any(items)
    both = and_ if sql else lambda *items: all(items)
    includes = (lambda value, choices: value.in_(choices)) if sql else lambda value, choices: value in choices
    # SQL LIKE would treat underscores as wildcards, so use an exact prefix.
    prefix = (lambda value, token: func.substr(value, 1, len(token)) == token) if sql else lambda value, token: value.startswith(token)
    recovery = either(prefix(subgroup, 'C_REVIEW_'), prefix(reason, 'REVIEW_SUPPORT_'))
    insufficient = either(decision == 'INSUFFICIENT_EVIDENCE', triage == 'INSUFFICIENT_EVIDENCE',
                          both(group == 'C', includes(subgroup, ('C_INSUFFICIENT_DATA',
                               'C_INSUFFICIENT_EVIDENCE', 'C_NO_VALID_IDENTITY_ANCHOR'))))
    unrecovered_floor = both(insufficient, ~recovery if sql else not recovery)
    return [
        (either(group == 'BLOCKED', includes(decision, _BLOCKED), includes(triage, _BLOCKED)), 'BLOCKED', 'BLOCKED'),
        (both(unrecovered_floor, subgroup == 'C_INSUFFICIENT_DATA'), 'C', 'C_MISSING_DATA'),
        (both(unrecovered_floor, either(subgroup == 'C_NO_VALID_IDENTITY_ANCHOR',
                                      reason == 'NO_VALID_IDENTITY_ANCHOR')), 'C', 'C_NO_ANCHOR'),
        (unrecovered_floor, 'C', 'C_INSUFFICIENT_EVIDENCE'),
        (either(disagreement == True, reason == 'AGENT_DISAGREEMENT'), 'B', 'B_AGENT_DISAGREEMENT'),
        (group == 'D', 'B', 'B_SOFT_CONFLICT'),
        (recovery, 'B', 'B_RECOVERED_SUPPORT'),
        (group == 'C', 'B', 'B_PARTIAL_EVIDENCE'),
        (both(group == 'A', subgroup == 'A3_NAME_PHONE_DOB'), 'A', 'A_MULTI_SIGNAL'),
        (both(group == 'A', subgroup == 'A3_NAME_GOV_ID'), 'A', 'A_IDENTIFIER'),
        (both(group == 'A', includes(subgroup, ('A1_NAME_PHONE', 'A2_NAME_EMAIL'))), 'A', 'A_CONTACT'),
        (group == 'A', 'A', 'A_PROPOSED_MATCH'),
    ]


def taxonomy_columns(group, subgroup, decision, triage, reason, disagreement=None):
    """Return SQLAlchemy ``(review_group, review_subgroup)`` CASE expressions.

    Supply the stored operative primary group, subgroup, decision, triage status,
    and effective reason (triage_reason, falling back to reason). Recovery must
    be represented by its persisted C_REVIEW_* subgroup or REVIEW_SUPPORT_*
    reason; payload-only support cannot be inferred from scalar columns. Pass
    the optional disagreement column when that flag is persisted separately.
    Uses only portable SQLite/PostgreSQL expressions and never reads payloads.
    """
    values = [func.coalesce(value, '') for value in (group, subgroup, decision, triage, reason)]
    rules = _routes(*values, func.coalesce(disagreement, false()) if disagreement is not None else false(), sql=True)
    return (case(*((condition, group_name) for condition, group_name, _ in rules),
                 else_='C').label('review_group'),
            case(*((condition, subgroup_name) for condition, _, subgroup_name in rules),
                 else_='C_INSUFFICIENT_EVIDENCE').label('review_subgroup'))


def taxonomy(result: Mapping) -> dict:
    """Describe one pair without mutating it or returning personal field values.

    Tags describe observed field states and explicit identifier relations
    separately. No relation, confidence, or evidence is inferred from a score.
    Unknown/free-text tags and field values are intentionally excluded.
    """
    support = result.get('review_support') or {}
    support = support if isinstance(support, Mapping) else {}
    reason = result.get('triage_reason') or result.get('reason') or ''
    subgroup = result.get('subgroup') or ''
    # Canonical assessments already persist these markers. Accommodate callers
    # with only the support object; SQL callers must project an equivalent marker.
    if support and not subgroup.startswith('C_REVIEW_') and not reason.startswith('REVIEW_SUPPORT_'):
        subgroup = 'C_REVIEW_SUPPORT'
    rules = _routes(result.get('primary_group') or '', subgroup, result.get('decision') or '',
                    result.get('triage_status') or '', reason, result.get('agent_disagreement') is True)
    group, subgroup = next(((group, subgroup) for condition, group, subgroup in rules if condition),
                           ('C', 'C_INSUFFICIENT_EVIDENCE'))
    tags = set()
    for field, entry in (result.get('comparisons') or {}).items():
        if field in _FIELDS and isinstance(entry, Mapping) and entry.get('state') in _STATES:
            tags.add(f'FIELD_STATE_{field.upper()}_{entry["state"]}')
    for entry in result.get('field_evidence') or []:
        if isinstance(entry, Mapping) and entry.get('field') in _FIELDS and entry.get('state') in _STATES:
            tags.add(f'FIELD_STATE_{entry["field"].upper()}_{entry["state"]}')
    relation = result.get('unverified_identifier_relation')
    if relation in _RELATIONS:
        tags.add('IDENTIFIER_RELATION_' + relation)
    if support.get('kind') in _SUPPORT:
        tags.add('REVIEW_SUPPORT_' + support['kind'])
    if result.get('agent_disagreement') is True or reason == 'AGENT_DISAGREEMENT':
        tags.add('AGENT_DISAGREEMENT')
    return {'review_group': group, 'review_subgroup': subgroup, 'evidence_tags': sorted(tags)}


def mvp_columns(group, subgroup):
    """Map the effective actions taxonomy to the four Step 2 review groups."""
    return (case((group == 'A', case((subgroup == 'A_CONTACT', 'A_CONTACT'),
                                       (subgroup.in_(('A_IDENTIFIER', 'A_MULTI_SIGNAL')), 'A_STRONG'), else_='B_SUPPORT')),
                 (group == 'B', case((subgroup.in_(('B_AGENT_DISAGREEMENT', 'B_SOFT_CONFLICT')), 'B_CONFLICT'),
                                     else_='B_SUPPORT')), else_=group).label('mvp_group'),
            subgroup.label('mvp_subgroup'))


def mvp_taxonomy(result):
    action = taxonomy(result)
    group, subgroup = action['review_group'], action['review_subgroup']
    if group == 'A':
        group = ('A_CONTACT' if subgroup == 'A_CONTACT' else
                 'A_STRONG' if subgroup in ('A_IDENTIFIER', 'A_MULTI_SIGNAL') else 'B_SUPPORT')
    elif group == 'B':
        group = 'B_CONFLICT' if subgroup in ('B_AGENT_DISAGREEMENT', 'B_SOFT_CONFLICT') else 'B_SUPPORT'
    return {'mvp_group': group, 'mvp_subgroup': subgroup}
