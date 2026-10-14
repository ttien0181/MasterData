"""Shared eligibility and ordering for golden drafts and exported contacts."""
from datetime import UTC, datetime

QUALITY = {'VALID': 3, 'UNKNOWN': 2, 'PARTIAL': 1, 'SHARED': 0,
           'INVALID': 0, 'PLACEHOLDER': 0, 'MISSING': 0}
CONTACTS = frozenset(('phone', 'email'))
EXPLICIT = frozenset(('STEWARD_OVERRIDE', 'SOURCE_CHOICE'))


def source_timestamp(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


def eligible_primary(field: str, observation: dict) -> bool:
    if not observation.get('value'):
        return False
    if observation.get('provenance') in EXPLICIT:
        return True
    quality = str(observation.get('quality') or 'UNKNOWN').upper()
    if quality in {'MISSING', 'INVALID', 'PLACEHOLDER'}:
        return False
    return field not in CONTACTS or not (quality == 'SHARED' or observation.get('shared_risk'))


def rank_with_priorities(observation, priorities):
    updated = source_timestamp(observation.get('observed_at'))
    return (not observation.get('verified', False), -QUALITY.get(observation.get('quality'), 2),
            priorities.get(observation.get('source_system'), 99), updated is None,
            -updated.timestamp() if updated else 0, observation.get('source_system') or '',
            observation.get('external_id') or '', observation.get('source_record_id') or '',
            str(observation.get('value') or ''))


def observation_rank(field: str, observation: dict, policy) -> tuple:
    priorities = dict(dict(policy.field_source_priority).get(field, policy.source_priority))
    return rank_with_priorities(observation, priorities)


def primary_values(field: str, data: dict) -> list[dict]:
    """Explicit selected values first; untouched shared alternatives stay out."""
    result, seen = [], set()
    candidates = []
    if data.get('provenance') in EXPLICIT:
        selected = data.get('value')
        selected = selected if isinstance(selected, list) else [selected]
        candidates.extend({'value': value, 'provenance': data['provenance']} for value in selected if value)
    if data.get('values'):
        candidates.extend(data['values'])
    elif data.get('value') and data.get('provenance') not in EXPLICIT:
        candidates.append(data)
    for observation in candidates:
        value = observation.get('value')
        if eligible_primary(field, observation) and value not in seen:
            result.append(observation)
            seen.add(value)
    return result
