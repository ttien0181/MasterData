"""Presentation routing stays independent of the operative matcher groups."""
from copy import deepcopy
from itertools import product

import pytest
from sqlalchemy import Boolean, Column, MetaData, String, Table, create_engine, select
from sqlalchemy.dialects import postgresql

from masterone.mdm.review_taxonomy import taxonomy, taxonomy_columns


@pytest.mark.parametrize('pair,want', [
    ({'primary_group': 'A', 'subgroup': 'A1_NAME_PHONE'}, ('A', 'A_CONTACT')),
    ({'primary_group': 'A', 'subgroup': 'A3_NAME_PHONE_DOB'}, ('A', 'A_MULTI_SIGNAL')),
    ({'primary_group': 'A', 'subgroup': 'A3_NAME_GOV_ID'}, ('A', 'A_IDENTIFIER')),
    ({'primary_group': 'A'}, ('A', 'A_PROPOSED_MATCH')),
    ({'primary_group': 'D'}, ('B', 'B_SOFT_CONFLICT')),
    ({'primary_group': 'C', 'decision': 'REVIEW'}, ('B', 'B_PARTIAL_EVIDENCE')),
    ({'primary_group': 'C', 'decision': 'INSUFFICIENT_EVIDENCE'}, ('C', 'C_INSUFFICIENT_EVIDENCE')),
    ({'primary_group': 'C', 'subgroup': 'C_INSUFFICIENT_DATA'}, ('C', 'C_MISSING_DATA')),
    ({'primary_group': 'C', 'subgroup': 'C_NO_VALID_IDENTITY_ANCHOR'}, ('C', 'C_NO_ANCHOR')),
    ({'decision': 'INSUFFICIENT_EVIDENCE', 'reason': 'NO_VALID_IDENTITY_ANCHOR'}, ('C', 'C_NO_ANCHOR')),
    ({'decision': 'INSUFFICIENT_EVIDENCE', 'subgroup': 'C_INSUFFICIENT_DATA'}, ('C', 'C_MISSING_DATA')),
    ({'primary_group': 'D', 'decision': 'INSUFFICIENT_EVIDENCE'}, ('C', 'C_INSUFFICIENT_EVIDENCE')),
    ({'primary_group': 'C', 'subgroup': 'C_REVIEW_NAME_FULL_DOB', 'decision': 'INSUFFICIENT_EVIDENCE'}, ('B', 'B_RECOVERED_SUPPORT')),
    ({'primary_group': 'A', 'reason': 'AGENT_DISAGREEMENT'}, ('B', 'B_AGENT_DISAGREEMENT')),
    ({'primary_group': 'A', 'agent_disagreement': True}, ('B', 'B_AGENT_DISAGREEMENT')),
    ({'primary_group': 'D', 'triage_status': 'BLOCKED'}, ('BLOCKED', 'BLOCKED')),
    ({'primary_group': 'BLOCKED', 'decision': 'REVIEW'}, ('BLOCKED', 'BLOCKED')),
    ({'decision': 'NON_MATCH', 'agent_disagreement': True}, ('BLOCKED', 'BLOCKED')),
    ({}, ('C', 'C_INSUFFICIENT_EVIDENCE')),
])
def test_actionable_routing_has_explicit_precedence(pair, want):
    result = taxonomy(pair)
    assert (result['review_group'], result['review_subgroup']) == want


def test_tags_keep_field_states_separate_from_actual_identifier_relation_and_ignore_pii():
    pair = {'primary_group': 'D', 'score': 1, 'name': 'Private Person',
            'comparisons': {'name': {'state': 'HS', 'left': 'Private Person'},
                            'government_id': {'state': 'NC'}, 'phone': {'state': 'CF'}},
            'unverified_identifier_relation': 'SAME_VALUE',
            'evidence_tags': ['Private Person'], 'agent_disagreement': True}
    original = deepcopy(pair)
    assert taxonomy(pair)['evidence_tags'] == [
        'AGENT_DISAGREEMENT', 'FIELD_STATE_GOVERNMENT_ID_NC',
        'FIELD_STATE_NAME_HS', 'FIELD_STATE_PHONE_CF', 'IDENTIFIER_RELATION_SAME_VALUE']
    assert pair == original
    assert taxonomy({'score': 1})['evidence_tags'] == []


def test_field_evidence_and_recovery_are_finite_tags():
    result = taxonomy({'primary_group': 'C', 'review_support': {'kind': 'NAME_FULL_DOB'},
                       'field_evidence': [{'field': 'name', 'state': 'EX'},
                                          {'field': 'Private Person', 'state': 'EX'}]})
    assert result['review_subgroup'] == 'B_RECOVERED_SUPPORT'
    assert result['evidence_tags'] == ['FIELD_STATE_NAME_EX', 'REVIEW_SUPPORT_NAME_FULL_DOB']


def test_sql_routes_match_payload_routes_for_nulls_and_conflicting_metadata():
    table = Table('pairs', MetaData(), *(Column(name, String) for name in
                  ('group', 'subgroup', 'decision', 'triage', 'reason')), Column('disagreement', Boolean))
    engine = create_engine('sqlite://')
    table.metadata.create_all(engine)
    rows = [dict(zip(('group', 'subgroup', 'decision', 'triage', 'reason', 'disagreement'), values))
            for values in product((None, 'A', 'C', 'D', 'BLOCKED'),
                                  (None, 'A1_NAME_PHONE', 'A3_NAME_PHONE_DOB', 'A3_NAME_GOV_ID', 'C_REVIEW_NAME_FULL_DOB',
                                   'C_INSUFFICIENT_DATA', 'C_NO_VALID_IDENTITY_ANCHOR'),
                                  (None, 'REVIEW', 'INSUFFICIENT_EVIDENCE', 'NOT_MERGEABLE'),
                                  (None, 'BLOCKED', 'INSUFFICIENT_EVIDENCE'),
                                  (None, 'AGENT_DISAGREEMENT', 'REVIEW_SUPPORT_NAME_FULL_DOB', 'NO_VALID_IDENTITY_ANCHOR'), (None, False, True))]
    columns = taxonomy_columns(*(table.c[name] for name in ('group', 'subgroup', 'decision', 'triage', 'reason', 'disagreement')))
    statement = select(*table.c, *columns)
    assert 'CASE' in str(statement.compile(dialect=postgresql.dialect()))
    with engine.begin() as connection:
        connection.execute(table.insert(), rows)
        for row in connection.execute(statement):
            pair = dict(zip(('primary_group', 'subgroup', 'decision', 'triage_status', 'reason', 'agent_disagreement'), row[:6]))
            result = taxonomy(pair)
            assert tuple(row[6:]) == (result['review_group'], result['review_subgroup'])
    engine.dispose()
