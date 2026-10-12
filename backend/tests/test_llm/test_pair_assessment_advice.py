import pytest

from masterone.llm.policy import PolicyDenied, allowed_payload, validate_output


def payload():
    return {'pair_token': 'pair-1', 'left': {'name': 'NGUYEN VAN AN', 'address': '12 PHO HUE'},
            'right': {'name': 'NGUYEN V AN', 'address': '12 PHO HUE'},
            'signals': {'name': 'HS', 'phone': 'MI', 'email': 'MI', 'dob': 'EX', 'address': 'EX', 'government_id': 'NC'},
            'unverified_identifier_relation': 'DIFFERENT_VALUE'}


def test_pair_advice_allows_only_approved_text_and_comparison_signals():
    assert allowed_payload('PAIR_ASSESSMENT', payload()) == payload()
    for key in ('phone', 'email', 'government_id', 'dob', 'raw_fields', 'gold'):
        value = payload()
        value['left'][key] = 'private-value'
        with pytest.raises(PolicyDenied):
            allowed_payload('PAIR_ASSESSMENT', value)
    value = payload()
    value['left']['address'] = 'contact somebody@example.com'
    with pytest.raises(PolicyDenied):
        allowed_payload('PAIR_ASSESSMENT', value)


def test_pair_advice_schema_rejects_hallucinated_refs_and_decisions():
    output = {'pair_token': 'pair-1', 'recommendation': 'LIKELY_SAME_PERSON',
              'evidence': [{'field':'name','state':'HS'},{'field':'address','state':'EX'}],
              'reason_codes': ['CORROBORATING_SIGNALS'], 'limitation_codes':['SOURCE_NOT_VERIFIED','HUMAN_REVIEW_REQUIRED']}
    assert validate_output('PAIR_ASSESSMENT', output, payload()) == output
    for changed in ({'recommendation': 'ACCEPTED'}, {'pair_token': 'other'},
                    {'evidence': [{'field':'phone','state':'EX'}]}, {'reason_vi': 'Gộp ngay.'},
                    {'reason_codes': ['NORMALIZATION_VARIANT']}):
        with pytest.raises(PolicyDenied):
            validate_output('PAIR_ASSESSMENT', {**output, **changed}, payload())


def test_advice_cannot_claim_phone_match_or_verification_not_present_in_evidence():
    value = payload()
    value['signals']['phone'] = 'DF'
    output = {'pair_token': 'pair-1', 'recommendation':'LIKELY_SAME_PERSON',
              'evidence':[{'field':'phone','state':'EX'}], 'reason_codes':['CORROBORATING_SIGNALS'],
              'limitation_codes':['SOURCE_NOT_VERIFIED','HUMAN_REVIEW_REQUIRED']}
    with pytest.raises(PolicyDenied):
        validate_output('PAIR_ASSESSMENT', output, value)
    output['evidence'][0]['state'] = 'DF'
    with pytest.raises(PolicyDenied):
        validate_output('PAIR_ASSESSMENT', output, value)
