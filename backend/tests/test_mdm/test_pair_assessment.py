"""Assessment policy is separate from historical baselines and human decisions."""
import pytest

from masterone.mdm.pipeline.standardization import standardize


@pytest.mark.parametrize('value, expected', [('true', 'verified'), ('false', 'unverified'), ('garbage', 'unknown')])
def test_verification_is_explicit_not_format_or_truthiness(value, expected):
    nf, _ = standardize({'name': 'Nguyen Van An', 'government_id': '001090123456',
                         'government_id_verified': value, 'government_id_namespace': 'VN'})
    assert nf.get('government_id_verification_status') == expected
    assert nf.get('_government_id_verified') is (expected == 'verified')
    assert nf.get('government_id_namespace') == 'VN'


def test_valid_identifier_without_verification_remains_unknown():
    nf, _ = standardize({'name': 'Nguyen Van An', 'government_id': '001090123456'})
    assert nf['_field_quality']['government_id'] == 'VALID'
    assert nf.get('government_id_verification_status') == 'unknown'
    assert nf.get('_government_id_verified') is False


def record(rid, **fields):
    raw = {'name': 'Nguyen Van An', 'phone': '0912345678', **fields}
    nf, flags = standardize(raw)
    return dict(id=rid, entity_type='PERSON', raw_fields=raw, normalized_fields=nf, quality_flags=flags)


def test_unverified_identifier_conflict_is_reviewable_not_a_hard_constraint():
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    result = assess_pair(record('a', government_id='001090123456'), record('b', government_id='001090123457'))
    assert result['decision'] == 'REVIEW'
    assert result['primary_group'] == 'D'
    assert result['reason'] != 'SAME_TYPE_GOV_ID_CONFLICT'
    assert result['constraint_codes'] == []


def test_verified_identifier_conflict_remains_blocked():
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    common = dict(government_id_verified='true', government_id_namespace='VN')
    result = assess_pair(record('a', government_id='001090123456', **common),
                         record('b', government_id='001090123457', **common))
    assert result['decision'] == 'NOT_MERGEABLE'
    assert result['primary_group'] == 'BLOCKED'
    assert 'VERIFIED_GOV_ID_CONFLICT' in result['constraint_codes']


def test_specific_subgroup_precedes_name_phone_and_missing_anchor_has_group():
    from masterone.mdm.pipeline.pair_assessment import assess_pair
    result = assess_pair(record('a', dob='1990-01-02'), record('b', dob='1990-01-02'))
    assert result['subgroup'] == 'A3_NAME_PHONE_DOB'
    result = assess_pair(record('a', phone=''), record('b', phone=''))
    assert result['primary_group'] == 'C'
    assert result['subgroup'] == 'C_NO_VALID_IDENTITY_ANCHOR'


def test_cannot_link_and_old_profile_are_preserved():
    from masterone.mdm.pipeline.pair_assessment import assess_pair, assessment_profile
    original = {'decision_semantics_v2_enabled': False, 'cluster_safety_v2_enabled': False,
                'generation_staging_enabled': False}
    upgraded = assessment_profile(original)
    assert original['decision_semantics_v2_enabled'] is False
    assert upgraded['decision_semantics_v2_enabled'] is True
    result = assess_pair(record('a'), record('b'), negative_pairs=frozenset({('a', 'b')}))
    assert result['primary_group'] == 'BLOCKED'
    assert result['reason'] == 'STEWARD_NEGATIVE_DECISION'
