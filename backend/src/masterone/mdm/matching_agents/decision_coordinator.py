"""Approved conservative R1, applied before canonical routing and eligibility."""
import math
from copy import deepcopy

from masterone.mdm.pipeline.decision_semantics import ReviewDecision


def coordinate_pair(rule: dict, embedding: dict | None, review: str, constraints: dict, config: dict) -> dict:
    ReviewDecision(review)
    result = deepcopy(rule)
    codes = list(dict.fromkeys(constraints.get('codes', [])))
    if constraints.get('cannot_link') or review == 'NON_MATCH':
        codes.append('STEWARD_NEGATIVE_DECISION')
    hard = bool(codes)
    veto = review in ('REJECTED', 'NON_MATCH')
    floor = result.get('decision') == 'INSUFFICIENT_EVIDENCE' and not result.get('review_support')
    tags = list(result.get('evidence_tags', []))
    disagreement = False
    if hard:
        result.update(decision='NOT_MERGEABLE', triage_status='BLOCKED', reason=codes[0])
    elif review == 'UNDECIDED' and not floor:
        for field in ('name', 'address'):
            evidence = (embedding or {}).get(field, {})
            if evidence.get('status') != 'OK':
                continue
            value = evidence.get('cosine')
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError('COORDINATOR_INVALID_COSINE')
            state = result.get('comparisons', {}).get(field, {}).get('state')
            if (state in ('EX', 'NX', 'NA', 'HS') and value <= config['coordinator']['low']
                    or state == 'CF' and value >= config['coordinator']['high']):
                disagreement = True
                tags.append('AGENT_DISAGREEMENT_' + field.upper())
        if disagreement and result.get('decision') not in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH'):
            result.update(decision='REVIEW', triage_status='REVIEW', reason='AGENT_DISAGREEMENT',
                          triage_reason='AGENT_DISAGREEMENT')
    blocked = hard or veto or result.get('decision') in ('BLOCKED', 'NOT_MERGEABLE', 'NON_MATCH')
    allowed = not blocked and (not floor or review == 'ACCEPTED')
    result.update(coordinator_version=config['coordinator']['version'], calibrated_probability=None,
                  embedding_evidence=deepcopy(embedding or {}),
                  steward_decision=review, identity_label='NON_MATCH' if review == 'NON_MATCH' else None,
                  agent_disagreement=disagreement, evidence_tags=sorted(set(tags)),
                  eligibility={'manual_allowed': allowed, 'auto_allowed': False,
                               'blocked_reasons': codes or (['REVIEW_VETO'] if veto else
                                                          ['INSUFFICIENT_EVIDENCE'] if not allowed else [])},
                  coordinator_trace=['HARD_CONSTRAINTS_DENY', 'STEWARD_OVERLAY', 'EVIDENCE_SUFFICIENCY',
                                     'DISAGREEMENT_SOFT_CONFLICT', 'PROFILE_RECOMMENDATION', 'REVIEW_ROUTING'])
    result['routing'] = ('BLOCKED' if blocked else 'DEFERRED' if floor and review != 'ACCEPTED'
                         else 'HUMAN_REVIEW' if review in ('UNDECIDED', 'DEFERRED') else 'HISTORICAL_DECISION')
    return result


def coordinate_assessment(payload: dict, evidence: dict, overlays: dict, config: dict) -> dict:
    result = {**payload, 'pairs': []}
    index = {row['pair_id']: row for row in evidence['pairs']}
    if len(index) != len(evidence['pairs']) or set(index) != {p['id'] for p in payload['pairs']}:
        raise ValueError('COORDINATOR_PAIR_COVERAGE_MISMATCH')
    for pair in payload['pairs']:
        row = index[pair['id']]
        coordinated = coordinate_pair(pair, row.get('fields'), overlays.get(pair['id'], 'UNDECIDED'), row['constraints'], config)
        if config.get('experiment') == 'E2':
            fields = row.get('fields') or {}
            available = all(fields.get(f, {}).get('status') == 'OK' for f in ('name', 'address'))
            if available and any(not isinstance(fields[f].get('cosine'), (int, float))
                                 or not math.isfinite(fields[f]['cosine']) or not -1 <= fields[f]['cosine'] <= 1
                                 for f in ('name', 'address')):
                raise ValueError('COORDINATOR_INVALID_COSINE')
            raw = ('MATCH' if all(fields[f]['cosine'] >= config['coordinator']['high'] for f in ('name', 'address'))
                   else 'NON_MATCH') if available else 'INSUFFICIENT_EVIDENCE'
            blocked = bool(row['constraints'].get('codes') or row['constraints'].get('cannot_link')
                           or overlays.get(pair['id']) in ('NON_MATCH', 'REJECTED'))
            coordinated['embedding_diagnostic'] = {'raw_decision': raw, 'guarded_decision': 'BLOCKED' if blocked else raw,
                                                    'production_identity_decision': False}
            coordinated['eligibility'].update(manual_allowed=False, auto_allowed=False,
                                               blocked_reasons=['EMBEDDING_ONLY_DIAGNOSTIC'])
        result['pairs'].append(coordinated)
    result['coordinator'] = config['coordinator']
    return result
