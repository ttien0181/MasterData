"""Golden eligibility must survive selection, rebuild and CSV export."""
from copy import deepcopy
from dataclasses import replace
from itertools import permutations
from types import SimpleNamespace

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.golden_service import selected_golden
from masterone.mdm.pipeline.cluster_guards import _shared_value_guard
from masterone.mdm.pipeline.cluster_policy import ClusterPolicy
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.workflow_exports import field_value


def observation(rid, source, field, value, quality='VALID', verified=False, updated=None):
    nf = {field: value, '_field_quality': {field: quality}, f'_{field}_verified': verified}
    if field in ('phone', 'email'):
        nf['phones' if field == 'phone' else 'emails'] = [dict(value=value, quality=quality, verified=verified)]
    return dict(id=rid, external_id=rid, source_system=source, raw_fields={field: value},
                normalized_fields=nf, source_updated_at=updated)


def golden(records, policy=None, existing_golden=None):
    with engine_context({'cluster_safety_v2_enabled': True, 'decision_semantics_v2_enabled': True}):
        return build_golden([r['id'] for r in records], {r['id']: r for r in records},
                            policy=policy, existing_golden=existing_golden)


@pytest.mark.parametrize('field', ['name', 'address', 'dob', 'government_id', 'phone', 'email'])
@pytest.mark.parametrize('bad', ['INVALID', 'PLACEHOLDER', 'MISSING'])
def test_verified_invalid_or_placeholder_never_beats_valid_value(field, bad):
    bad_rec = observation('a', 'CRM', field, 'bad value', bad, True)
    good = observation('b', 'ERP', field, 'good value')
    assert golden([bad_rec, good])['fields'][field]['value'] == 'good value'


def test_verified_valid_lower_priority_source_wins():
    a = observation('a', 'CRM', 'name', 'CRM name')
    b = observation('b', 'BILL', 'name', 'Verified name', verified=True)
    assert golden([a, b])['fields']['name']['value'] == 'Verified name'


def test_quality_precedes_source_and_unverified_recency():
    a = observation('a', 'CRM', 'name', 'Unknown name', 'UNKNOWN', updated='2026-01-01T00:00:00Z')
    b = observation('b', 'ERP', 'name', 'Valid name', updated='2020-01-01T00:00:00Z')
    assert golden([a, b])['fields']['name']['value'] == 'Valid name'


def test_shared_contact_retained_as_alternative_but_never_exported_by_default():
    a = observation('a', 'CRM', 'email', 'info@example.test', 'SHARED', True)
    b = observation('b', 'ERP', 'email', 'personal@example.test')
    field = golden([a, b])['fields']['email']
    assert field['value'] == 'personal@example.test'
    assert [v['value'] for v in field['values']] == ['personal@example.test']
    assert 'info@example.test' in [v['value'] for v in field['alternatives']]
    assert field_value('email', field) == 'personal@example.test'


def test_shared_only_contact_has_blank_primary_and_reviewable_alternative():
    field = golden([observation('a', 'CRM', 'email', 'info@example.test', 'SHARED')])['fields']['email']
    assert field['value'] is None
    assert field['values'] == []
    assert field['alternatives'][0]['value'] == 'info@example.test'
    assert field_value('email', field) == ''


def test_explicit_shared_risk_cannot_become_primary_even_with_valid_quality():
    a = observation('a', 'CRM', 'phone', '0912345678', verified=True)
    a['normalized_fields']['phones'][0]['shared_risk'] = True
    b = observation('b', 'ERP', 'phone', '0987654321')
    assert golden([a, b])['fields']['phone']['value'] == '0987654321'


def test_raw_contact_fallback_cannot_launder_invalid_quality():
    a = observation('a', 'CRM', 'email', 'bad email', 'INVALID')
    a['normalized_fields']['email'] = ''
    a['normalized_fields']['emails'] = []
    b = observation('b', 'ERP', 'email', 'personal@example.test', 'UNKNOWN')
    assert golden([a, b])['fields']['email']['value'] == 'personal@example.test'


def test_per_field_source_priority_is_used_with_common_fallback():
    policy = replace(ClusterPolicy(), field_source_priority=(('address', (('ERP', 0), ('CRM', 1))),))
    a = observation('a', 'CRM', 'address', 'CRM address')
    b = observation('b', 'ERP', 'address', 'ERP address')
    assert golden([a, b], policy)['fields']['address']['value'] == 'ERP address'
    a = observation('a', 'CRM', 'name', 'CRM name')
    b = observation('b', 'ERP', 'name', 'ERP name')
    assert golden([a, b], policy)['fields']['name']['value'] == 'CRM name'


def test_real_timezone_recency_and_input_permutations():
    a = observation('a', 'CRM', 'name', 'Old', updated='2024-01-01T00:00:00Z')
    b = observation('b', 'CRM', 'name', 'New', updated='2024-01-01T08:00:00+07:00')
    c = observation('c', 'CRM', 'name', 'No source time', updated='2026-01-01')
    c['external_id'] = 'ZZZ'
    for order in permutations([a, b, c]):
        assert golden(list(order))['fields']['name']['value'] == 'New'


def test_manual_override_survives_rebuild_and_is_exported():
    records = [observation('a', 'CRM', 'email', 'info@example.test', 'SHARED')]
    result = golden(records, existing_golden={'_golden_override': {'email': 'Chosen contact'}})
    assert field_value('email', result['fields']['email']) == 'Chosen contact'
    assert golden(records, existing_golden=deepcopy(result)) == result


def test_contact_list_override_can_be_exported_without_hashing_a_list():
    result = golden([observation('a', 'CRM', 'email', 'info@example.test', 'SHARED')],
                    existing_golden={'_golden_override': {'email': ['one@example.test', 'two@example.test']}})
    assert field_value('email', result['fields']['email']) == 'one@example.test; two@example.test'


def test_manual_selection_does_not_inherit_source_verification_or_source_system():
    cluster = SimpleNamespace(golden={'fields': {'name': {'value': 'Verified source', 'verified': True,
                              'source_system': 'CRM', 'quality': 'VALID', 'observed_at': '2024-01-01T00:00:00Z'}}},
                              selections={'name': {'selection_mode': 'MANUAL_OVERRIDE', 'value': 'Typed name'}})
    field = selected_golden(None, cluster)['fields']['name']
    assert field['value'] == 'Typed name'
    assert field['verified'] is False
    assert field['quality'] == 'STEWARD'
    assert field['source_system'] is None
    assert field['observed_at'] is None


def test_source_choice_verification_follows_selected_source_even_with_raw_whitespace(client, workflow_run):
    from masterone.mdm.models import ResolutionRecord

    from tests.test_mdm.test_workflow_stages import ready_decisions

    ready_decisions(client, workflow_run)
    with client.session_factory.begin() as db:
        record = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
        record.raw_fields = {**record.raw_fields, 'name': ' Source name '}
        record.normalized_fields = {**record.normalized_fields, '_name_verified': False}
        cluster = SimpleNamespace(golden={'fields': {'name': {'value': 'Old source', 'verified': True}}},
                                  selections={'name': {'selection_mode': 'SOURCE_CHOICE', 'source_record_id': record.id}})
        field = selected_golden(db, cluster)['fields']['name']
        assert field['value'] == ' Source name '
        assert field['verified'] is False
        assert field['quality'] == 'VALID'


def test_df_contact_does_not_produce_shared_warning():
    pair = dict(left_id='a', right_id='b', comparisons={'phone': {'state': 'DF'}, 'email': {'state': 'MI'}})
    assert _shared_value_guard(frozenset({'a'}), frozenset({'b'}), [pair]) is None
    pair['comparisons']['phone']['state'] = 'SH'
    assert _shared_value_guard(frozenset({'a'}), frozenset({'b'}), [pair]).severity == 'WARN'


def test_old_contact_payload_export_filters_shared_alternative():
    field = dict(value='personal@example.test', values=[
        dict(value='personal@example.test', quality='VALID'),
        dict(value='info@example.test', quality='SHARED', shared_risk=True)])
    assert field_value('email', field) == 'personal@example.test'


def test_legacy_new_build_does_not_use_external_id_as_time():
    a = observation('a', 'CRM', 'name', 'No source time')
    b = observation('b', 'CRM', 'name', 'Real source time', updated='2020-01-01T00:00:00Z')
    a['external_id'] = 'ZZZ'
    with engine_context({'cluster_safety_v2_enabled': False}):
        result = build_golden(['a', 'b'], {'a': a, 'b': b})
    assert result['fields']['name']['value'] == 'Real source time'
