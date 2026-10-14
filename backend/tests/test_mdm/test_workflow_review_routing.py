"""Retained weak candidates are not mandatory work or publication bridges."""
from copy import deepcopy

import pytest

from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_cluster_preview import pending_regions
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review_rules import review_required

from tests.test_mdm.test_pair_classification_regressions import record
from tests.test_mdm.test_workflow_partial_publication import edge, partial_result, snapshot
from tests.test_mdm.test_workflow_stages import ready_decisions


@pytest.mark.parametrize('fields', [
    dict(name='Tran Thi Binh'),
    dict(dob='1981-01-01'),
    dict(government_id='001090123457'),
])
def test_floor_failed_conflict_preserves_insufficient_evidence(fields):
    a = record('a', phone='', email='', dob='1990', address='Ha Noi', government_id='001090123456')
    b = record('b', phone='', email='', **{'dob':'1990','address':'Ha Noi',**fields})
    before = deepcopy([a, b])
    result = assess_pair(a, b)
    assert result['rule_conclusion'] == 'INSUFFICIENT_EVIDENCE'
    assert result['decision'] == result['triage_status'] == 'INSUFFICIENT_EVIDENCE'
    assert result['reason'] == 'NO_VALID_IDENTITY_ANCHOR'
    assert (result['primary_group'], result['subgroup']) == ('C', 'C_NO_VALID_IDENTITY_ANCHOR')
    assert result['field_evidence']
    assert not result['constraint_codes']
    assert [a, b] == before


@pytest.mark.parametrize('decision,baseline,triage,want', [
    ('UNDECIDED', 'INSUFFICIENT_EVIDENCE', 'INSUFFICIENT_EVIDENCE', False),
    ('UNDECIDED', 'INSUFFICIENT_EVIDENCE', 'REVIEW', False),
    ('DEFERRED', 'INSUFFICIENT_EVIDENCE', 'INSUFFICIENT_EVIDENCE', True),
    ('UNDECIDED', 'REVIEW', 'REVIEW', True),
    ('UNDECIDED', 'MERGEABLE', 'REVIEW', True),
    ('UNDECIDED', 'MERGEABLE', 'AUTO_MERGE', False),
    ('UNDECIDED', 'NOT_MERGEABLE', 'REVIEW', False),
    ('UNDECIDED', None, None, True),
    ('UNDECIDED', 'UNKNOWN_HISTORY', None, True),
    ('ACCEPTED', 'REVIEW', 'REVIEW', False),
    ('REJECTED', 'INSUFFICIENT_EVIDENCE', None, False),
    ('NON_MATCH', 'REVIEW', 'REVIEW', False),
])
def test_mandatory_review_matrix(decision, baseline, triage, want):
    assert review_required(decision, {'decision': baseline, 'triage_status': triage}) is want


def test_api_summary_filter_and_workflow_blockers_use_effective_review_routing(client, workflow_run):
    ready_decisions(client, workflow_run)
    url = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        original = deepcopy(db.query(WorkflowPair).filter_by(run_id=workflow_run).first().baseline)
        db.query(WorkflowPair).filter_by(run_id=workflow_run).delete()
        cases = [
            ('weak', 'INSUFFICIENT_EVIDENCE', 'UNDECIDED', None),
            ('explicit-wait', 'INSUFFICIENT_EVIDENCE', 'DEFERRED', None),
            ('review', 'REVIEW', 'UNDECIDED', None),
            ('resolved', 'REVIEW', 'REJECTED', None),
            ('assessed-weak', 'REVIEW', 'UNDECIDED', 'INSUFFICIENT_EVIDENCE'),
            ('assessed-review', 'INSUFFICIENT_EVIDENCE', 'UNDECIDED', 'REVIEW'),
        ]
        for pid, conclusion, saved, assessment in cases:
            baseline = {**original, 'decision': conclusion, 'triage_status': conclusion,
                        'primary_group': 'C', 'subgroup': 'C_NO_VALID_IDENTITY_ANCHOR'}
            assessed = {**baseline, 'decision': assessment, 'triage_status': assessment} if assessment else None
            db.add(WorkflowPair(id=pid, run_id=workflow_run, baseline=baseline, assessment=assessed, decision=saved))
    required = client.get(url + '/pairs?resolution=REQUIRED').json()
    assert {p['pair_id'] for p in required['items']} == {'explicit-wait', 'review', 'assessed-review'}
    summary = client.get(url + '/pairs/summary').json()
    assert summary['total'] == 6
    assert summary['needs_review'] == 3
    assert summary['insufficient'] == 3
    assert summary['groups'][0]['pending'] == 3
    insufficient = client.get(url + '/pairs?resolution=INSUFFICIENT').json()
    assert {p['pair_id'] for p in insufficient['items']} == {'weak', 'explicit-wait', 'assessed-weak'}
    state = client.get(url).json()
    assert state['counts']['review_required']['value'] == 3
    assert state['counts']['insufficient']['value'] == 3
    assert next(b['count'] for b in state['blockers'] if b['code'] == 'PENDING_DECISIONS') == 3
    with client.session_factory.begin() as db:
        db.query(WorkflowPair).filter(WorkflowPair.run_id == workflow_run,
            WorkflowPair.id.in_(['explicit-wait', 'review', 'assessed-review'])).update({'decision': 'REJECTED'})
    state = client.get(url).json()
    assert not any(b['code'] == 'PENDING_DECISIONS' for b in state['blockers'])
    assert 'finalize-decisions' in state['allowed_actions']
    assert client.get(url + '/pairs?resolution=REQUIRED').json()['total'] == 0


def test_insufficient_bridge_does_not_hold_safe_group_or_spread_preview_region():
    a, b, x, y = [record(rid) for rid in ('a', 'b', 'x', 'y')]
    pairs = [edge(a, b), edge(b, x), edge(x, y)]
    pairs[1].update(decision='INSUFFICIENT_EVIDENCE', triage_status='INSUFFICIENT_EVIDENCE')
    source = snapshot([a, b, x, y], pairs, {pairs[0]['id']: 'DEFERRED', pairs[2]['id']: 'ACCEPTED'})
    result = partial_result(source)
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['x', 'y']]
    assert pending_regions(source) == [{'record_ids': ['a', 'b'], 'record_count': 2,
        'pending_pair_ids': [pairs[0]['id']], 'pending_pair_count': 1}]


def test_only_insufficient_candidates_publish_separate_records_without_human_decisions():
    a, b = record('a'), record('b')
    pair = {**edge(a, b), 'decision': 'INSUFFICIENT_EVIDENCE', 'triage_status': 'INSUFFICIENT_EVIDENCE'}
    source = snapshot([a, b], [pair])
    result = partial_result(source)
    assert not result['unresolved_record_ids']
    assert not result['unresolved_pair_ids']
    assert not result['conflicts']
    assert not pending_regions(source)
    from masterone.mdm.engine_adapter import engine_context
    from masterone.mdm.workflow_finalize import compute_publication
    with engine_context(dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True,
                             generation_staging_enabled=False)):
        published = compute_publication({**source, 'allow_partial': True}, 'weak-only', 1)
    assert sorted(c['member_record_ids'] for c in published['clusters']) == [['a'], ['b']]
    assert published['publication']['publication_status'] == 'COMPLETE'


def test_explicit_defer_on_insufficient_candidate_remains_pending():
    a, b = record('a'), record('b')
    pair = {**edge(a, b), 'decision': 'INSUFFICIENT_EVIDENCE', 'triage_status': 'INSUFFICIENT_EVIDENCE'}
    source = snapshot([a, b], [pair], {pair['id']: 'DEFERRED'})
    result = partial_result(source)
    assert result['unresolved_record_ids'] == ['a', 'b']
    assert result['unresolved_pair_ids'] == [pair['id']]
    assert pending_regions(source)[0]['record_ids'] == ['a', 'b']


def test_explicit_accept_on_insufficient_candidate_is_still_checked_and_linked():
    a, b = record('a'), record('b')
    pair = {**edge(a, b), 'decision': 'INSUFFICIENT_EVIDENCE', 'triage_status': 'INSUFFICIENT_EVIDENCE'}
    result = partial_result(snapshot([a, b], [pair], {pair['id']: 'ACCEPTED'}))
    assert not result['conflicts']
    assert [c['member_record_ids'] for c in result['safe_clusters']] == [['a', 'b']]
