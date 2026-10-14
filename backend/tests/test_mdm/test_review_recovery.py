"""Supported weak evidence creates manual work, never an automatic merge."""
from copy import deepcopy

import pytest

from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.pipeline.matching import compare_and_score_rule, value_frequencies
from masterone.mdm.workflow_models import WorkflowPair
from masterone.mdm.workflow_review import eligibility
from tests.test_mdm.test_pair_classification_regressions import record


def test_exact_full_name_and_complete_dob_recover_review_without_contact():
    a, b = record('a', phone='', email='', address=''), record('b', phone='', email='', address='')
    before = deepcopy([a, b])
    result = assess_pair(a, b)
    assert result['decision'] == result['triage_status'] == 'REVIEW'
    assert result['rule_conclusion'] == 'INSUFFICIENT_EVIDENCE'
    assert result['review_support']['kind'] == 'NAME_FULL_DOB'
    assert result['review_support']['fields'] == ['name', 'dob']
    assert result['primary_group'] == 'C'
    pair = WorkflowPair(id='p', run_id='r', baseline=result, decision='UNDECIDED')
    assert eligibility(pair)['manual_allowed'] and not eligibility(pair)['auto_allowed']
    assert [a, b] == before


def test_name_conflict_needs_dob_and_numbered_street_and_stays_manual_d():
    a = record('a', phone='', email='')
    b = record('b', name='Tran Thi Binh', phone='', email='')
    result = assess_pair(a, b)
    assert result['decision'] == 'REVIEW'
    assert result['review_support']['kind'] == 'FULL_DOB_DETAIL_ADDRESS'
    assert result['primary_group'] == 'D'
    assert eligibility(WorkflowPair(id='p', run_id='r', baseline=result, decision='UNDECIDED'))['reason_required']
    assert next(e for e in result['field_evidence'] if e['field'] == 'name')['state'] == 'CF'


@pytest.mark.parametrize('dob,address,name', [
    ('1990', '123 Nguyen Trai, Thanh Xuan, Ha Noi', 'Nguyen Van An'),
    ('1990-05', '123 Nguyen Trai, Thanh Xuan, Ha Noi', 'Nguyen Van An'),
    ('', '123 Nguyen Trai, Thanh Xuan, Ha Noi', 'Nguyen Van An'),
    ('1990-05-15', 'Ha Noi', 'Tran Thi Binh'),
    ('1990-05-15', 'Phuong 7, Ha Noi', 'Tran Thi Binh'),
    ('1990-05-15', '123 Phuong 7, Ha Noi', 'Tran Thi Binh'),
    ('1990-05-15', '123 Nguyen Trai, Thanh Xuan, Ha Noi', ''),
])
def test_unsupported_or_partial_combinations_remain_insufficient(dob, address, name):
    a = record('a', name=name, dob=dob, address=address, phone='', email='')
    b = record('b', name=name if name=='Nguyen Van An' else 'Le Van Cuong', dob=dob, address=address, phone='', email='')
    result = assess_pair(a, b)
    assert result['decision'] == 'INSUFFICIENT_EVIDENCE'
    assert not result.get('review_support')


@pytest.mark.parametrize('a_phone,b_phone', [('0912345678','0912345679'),('0912345678','0912345768')])
def test_phone_one_digit_or_transposition_plus_name_address_recovers_symmetrically(a_phone, b_phone):
    a = record('a', phone=a_phone, email='', dob='')
    b = record('b', phone=b_phone, email='', dob='')
    ab, ba = assess_pair(a, b), assess_pair(b, a)
    assert ab['decision'] == ba['decision'] == 'REVIEW'
    assert ab['review_support'] == ba['review_support']
    assert ab['review_support']['kind'] == 'NAME_NEAR_CONTACT_ADDRESS'
    assert ab['review_support']['fields'] == ['name', 'phone', 'address']
    assert ab['score'] == ba['score']
    assert ab['comparisons']['phone']['state'] == 'DF'


@pytest.mark.parametrize('flag', ['phone_shared','phone_shared_risk'])
def test_explicit_shared_contact_does_not_become_fuzzy_support(flag):
    a = record('a', phone='0912345678', email='', dob='', **{flag:True})
    b = record('b', phone='0912345679', email='', dob='')
    assert assess_pair(a,b)['decision'] == 'INSUFFICIENT_EVIDENCE'


def test_repeated_phone_without_dob_does_not_gain_soft_identity_support():
    a, b = record('a', email='', dob=''), record('b', email='', dob='')
    frequencies=value_frequencies([a,b]+[record(str(i), email='', dob='') for i in range(8)])
    assert assess_pair(a,b,frequencies)['decision'] == 'INSUFFICIENT_EVIDENCE'


def test_valid_unverified_same_identifier_is_review_support_not_verified_anchor():
    a = record('a', phone='', email='', dob='', address='', government_id='001090123456', government_id_namespace='VN')
    b = record('b', phone='', email='', dob='', address='', government_id='001090123456', government_id_namespace='VN')
    result=assess_pair(a,b)
    assert result['decision'] == 'REVIEW'
    assert result['review_support']['kind'] == 'NAME_UNVERIFIED_ID'
    assert result['comparisons']['government_id']['state'] == 'NC'
    assert result['rule_conclusion'] == 'INSUFFICIENT_EVIDENCE'


@pytest.mark.parametrize('identifier', ['000000000000','111111111111'])
def test_placeholder_identifiers_do_not_recover_review(identifier):
    a=record('a',phone='',email='',dob='',address='',government_id=identifier)
    b=record('b',phone='',email='',dob='',address='',government_id=identifier)
    assert assess_pair(a,b)['decision'] == 'INSUFFICIENT_EVIDENCE'


def test_verified_conflict_and_cannot_link_always_take_precedence():
    a=record('a',phone='',email='',government_id='001090123456',government_id_verified=True,government_id_namespace='VN')
    b=record('b',phone='',email='',government_id='001090123457',government_id_verified=True,government_id_namespace='VN')
    assert assess_pair(a,b)['decision'] == 'NOT_MERGEABLE'
    assert not assess_pair(a,b).get('review_support')
    b=record('b',phone='',email='')
    assert assess_pair(a,b,negative_pairs=frozenset({('a','b')}))['decision'] == 'NOT_MERGEABLE'


def test_conflicting_unverified_identifiers_are_not_recovered_by_name_dob_alone():
    a=record('a',phone='',email='',government_id='001090123456')
    b=record('b',phone='',email='',government_id='001090123457')
    assert assess_pair(a,b)['decision']=='INSUFFICIENT_EVIDENCE'
    # A real contact anchor still uses the existing D/reason policy.
    a=record('a',government_id='001090123456');b=record('b',government_id='001090123457')
    assert assess_pair(a,b)['decision']=='REVIEW' and assess_pair(a,b)['primary_group']=='D'


@pytest.mark.parametrize('kind,namespace', [('HOUSEHOLD_ID','SHARED'),('CCCD_12',''),('CCCD_12','OTHER')])
def test_arbitrary_or_missing_identifier_domain_is_not_person_support(kind,namespace):
    a=record('a',phone='',email='',dob='',address='',government_id='001090123456')
    b=record('b',phone='',email='',dob='',address='',government_id='001090123456')
    for r in (a,b):
        r['normalized_fields']['government_id_type']=kind
        r['normalized_fields']['government_id_namespace']=namespace
    assert assess_pair(a,b)['decision']=='INSUFFICIENT_EVIDENCE'


def test_custom_identifier_requires_explicit_person_uniqueness_on_both_sides():
    a=record('a',phone='',email='',dob='',address='',government_id='001090123456')
    b=record('b',phone='',email='',dob='',address='',government_id='001090123456')
    for r in (a,b):
        r['normalized_fields'].update(government_id_type='PERSON_ID',government_id_namespace='CUSTOM')
    a['raw_fields']['government_id_identity_unique']=True
    assert assess_pair(a,b)['decision']=='INSUFFICIENT_EVIDENCE'
    b['raw_fields']['government_id_identity_unique']=True
    assert assess_pair(a,b)['review_support']['kind']=='NAME_UNVERIFIED_ID'


def test_identifiers_in_different_explicit_namespaces_do_not_veto_name_dob_support():
    a=record('a',phone='',email='',address='',government_id='001090123456',government_id_namespace='VN')
    b=record('b',phone='',email='',address='',government_id='001090123457',government_id_namespace='OTHER')
    result=assess_pair(a,b)
    assert result['decision']=='REVIEW'
    assert result['review_support']['kind']=='NAME_FULL_DOB'
    # Existing grouping remains conservative; this grants no automatic permission.
    assert result['primary_group']=='D'


@pytest.mark.parametrize('right_email,expected', [('person1@example.net','REVIEW'),('person1@elsewhere.net','INSUFFICIENT_EVIDENCE')])
def test_near_email_requires_the_same_domain_and_supporting_address(right_email,expected):
    a=record('a',phone='',email='person2@example.net',dob='')
    b=record('b',phone='',email=right_email,dob='')
    ab,ba=assess_pair(a,b),assess_pair(b,a)
    assert ab['decision']==ba['decision']==expected
    if expected=='REVIEW':
        assert ab['review_support']==ba['review_support']
        assert ab['review_support']['fields']==['name','email','address']


@pytest.mark.parametrize('marker,value', [('historical',True),('valid_to','2020-01-01'),('origin_id','old-contact')])
def test_historical_near_contact_does_not_recover_review(marker,value):
    a=record('a',phone='0912345678',email='',dob='')
    b=record('b',phone='0912345679',email='',dob='')
    a['normalized_fields']['phones'][0][marker]=value
    assert assess_pair(a,b)['decision']=='INSUFFICIENT_EVIDENCE'


def test_secondary_near_contacts_are_symmetric_and_order_independent():
    a=record('a',phone='0987654321;0912345678',email='',dob='')
    b=record('b',phone='0965432109;0912345679',email='',dob='')
    before=assess_pair(a,b)
    for r in (a,b):
        r['normalized_fields']['phones'].reverse()
    after=assess_pair(b,a)
    assert before['decision']==after['decision']=='REVIEW'
    assert before['review_support']==after['review_support']
    assert before['review_support']['fields']==['name','phone','address']
