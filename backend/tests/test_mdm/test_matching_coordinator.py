"""R1 precedence hand examples; no probability and no embedding-only Accept."""
from copy import deepcopy

import pytest


def rule(decision='MERGEABLE', state='EX'):
    return {'decision': decision, 'score': 7.25, 'primary_group': 'A', 'subgroup': 'A1_NAME_PHONE',
            'comparisons': {'name': {'state': state}}, 'triage_status': 'REVIEW'}


def config():
    return {'coordinator': {'version': 'r1_v1', 'high': .85, 'low': .50}}


@pytest.mark.parametrize('review', ['UNDECIDED', 'ACCEPTED', 'REJECTED', 'NON_MATCH', 'DEFERRED'])
def test_hard_constraint_precedes_steward_and_cosine(review):
    from masterone.mdm.matching_agents.decision_coordinator import coordinate_pair
    original = rule()
    result = coordinate_pair(original, {'name': {'status': 'OK', 'cosine': 1.}}, review,
                             {'codes': ['VERIFIED_GOV_ID_CONFLICT'], 'cannot_link': False}, config())
    assert result['decision'] == 'NOT_MERGEABLE'
    assert not result['eligibility']['manual_allowed'] and not result['eligibility']['auto_allowed']
    assert result['steward_decision'] == review
    assert result['score'] == 7.25 and original == rule()
    assert result['calibrated_probability'] is None


@pytest.mark.parametrize('review', ['REJECTED', 'NON_MATCH'])
def test_veto_is_retained_without_inventing_negative_label(review):
    from masterone.mdm.matching_agents.decision_coordinator import coordinate_pair
    result = coordinate_pair(rule(), {}, review, {'codes': [], 'cannot_link': False}, config())
    assert not result['eligibility']['manual_allowed']
    assert result['steward_decision'] == review
    assert result['identity_label'] == ('NON_MATCH' if review == 'NON_MATCH' else None)


def test_insufficient_floor_then_disagreement_and_unavailable():
    from masterone.mdm.matching_agents.decision_coordinator import coordinate_pair
    missing = rule('INSUFFICIENT_EVIDENCE')
    high = {'name': {'status': 'OK', 'cosine': 1.}}
    result = coordinate_pair(missing, high, 'UNDECIDED', {}, config())
    assert result['decision'] == 'INSUFFICIENT_EVIDENCE'
    assert not result['eligibility']['manual_allowed']
    for original, embedding in [(rule(), {'name': {'status': 'OK', 'cosine': .50}}),
                                (rule('REVIEW', 'CF'), {'name': {'status': 'OK', 'cosine': .85}})]:
        snapshot = deepcopy(original)
        result = coordinate_pair(original, embedding, 'UNDECIDED', {}, config())
        assert result['decision'] == result['triage_status'] == 'REVIEW'
        assert 'AGENT_DISAGREEMENT_NAME' in result['evidence_tags']
        assert result['score'] == original['score'] and original == snapshot
    unavailable = coordinate_pair(rule(), {'name': {'status': 'NOT_AVAILABLE', 'cosine': None}}, 'UNDECIDED', {}, config())
    assert unavailable['decision'] == 'MERGEABLE' and not unavailable['agent_disagreement']
    accepted = coordinate_pair(missing, high, 'ACCEPTED', {}, config())
    assert accepted['steward_decision'] == 'ACCEPTED'
    assert accepted['eligibility']['manual_allowed']


def test_e2_ablation_is_diagnostic_and_cannot_accept_or_bypass_deny():
    from masterone.mdm.matching_agents.decision_coordinator import coordinate_assessment
    cfg = {**config(), 'experiment': 'E2'}
    fields = {f: {'status': 'OK', 'cosine': .90} for f in ('name', 'address')}
    pair = {**rule('INSUFFICIENT_EVIDENCE'), 'id': 'pair'}
    evidence = {'pairs': [{'pair_id': 'pair', 'fields': fields, 'constraints': {}}]}
    result = coordinate_assessment({'pairs': [pair]}, evidence, {}, cfg)['pairs'][0]
    assert result['embedding_diagnostic']['raw_decision'] == result['embedding_diagnostic']['guarded_decision'] == 'MATCH'
    assert not result['eligibility']['manual_allowed'] and not result['eligibility']['auto_allowed']
    result = coordinate_assessment({'pairs': [pair]}, evidence, {'pair': 'NON_MATCH'}, cfg)['pairs'][0]
    assert result['embedding_diagnostic']['guarded_decision'] == 'BLOCKED'
    evidence['pairs'][0]['fields']['address'] = {'status': 'NOT_AVAILABLE', 'cosine': None}
    result = coordinate_assessment({'pairs': [pair]}, evidence, {}, cfg)['pairs'][0]
    assert result['embedding_diagnostic']['raw_decision'] == 'INSUFFICIENT_EVIDENCE'
