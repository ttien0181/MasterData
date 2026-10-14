"""Controlled automation is certified per component cohort, never by pair score."""
from copy import deepcopy
from datetime import datetime,timezone
import importlib

import pytest

from masterone.mdm.generation_staging import digest
from masterone.mdm.pipeline.pair_assessment import assess_pair,assessment_profile
from masterone.mdm.pipeline.matching import value_frequencies
from tests.test_mdm.test_pair_classification_regressions import record

NOW=datetime(2026,10,13,tzinfo=timezone.utc)


def policy():
    return importlib.import_module('masterone.mdm.pipeline.pair_automation')


def certificate(cohorts=None):
    return dict(version='controlled-pair-automation-v1',assessment_version='person-review-assessment-v4',
        scope='production',independent_holdout=True,representative=True,precision_target=.995,
        validation_dataset_hash='d'*64,code_identity='a'*64,profile_identity='b'*64,
        owner_user_id='test-owner',data_scope='person-production',
        expires_at='2026-11-01T00:00:00+00:00',source_systems=['a','b','c'],
        cohorts=cohorts or {'c'*64:dict(correct=2000,labelled=2000,attempted=2000,constraint_violations=0)})


def validate(c):
    return policy().validate_certificate(c,code_identity='a'*64,profile_identity='b'*64,sources={'a','b'},now=NOW,
        owner_user_id='test-owner',data_scope='person-production')


def snapshot(size=2):
    records=[record(chr(97+i)) for i in range(size)]
    freq=value_frequencies(records);pairs=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            result=assess_pair(a,b,freq)
            assert result['decision']=='MERGEABLE'
            result['id']=a['id']+b['id'];pairs.append(result)
    return dict(records=records,pairs=pairs,profile=assessment_profile(),
                overlays={p['id']:'UNDECIDED' for p in pairs},negative_pairs=[],policy_version='person-review-assessment-v4')


def test_large_complete_independent_production_cohort_can_pass():
    r=validate(certificate())
    assert r['eligible'] and r['precision_target']==.995
    assert r['cohorts']['c'*64]['precision_lower_bound']>=.995


@pytest.mark.parametrize('change,reason',[
    ({'scope':'synthetic-benchmark'},'NON_PRODUCTION_VALIDATION'),
    ({'independent_holdout':False},'NO_INDEPENDENT_HOLDOUT'),
    ({'representative':False},'NON_REPRESENTATIVE_LABELS'),
    ({'code_identity':'x'*64},'CODE_CHANGED'),
    ({'profile_identity':'x'*64},'PROFILE_CHANGED'),
    ({'expires_at':'2026-10-12T00:00:00Z'},'CERTIFICATE_EXPIRED'),
    ({'precision_target':.99},'PRECISION_TARGET_MISMATCH'),
    ({'source_systems':['a']},'SOURCE_SCOPE_MISMATCH'),
    ({'cohorts':{}},'NO_CERTIFIED_COHORTS'),
    ({'owner_user_id':'another-owner'},'OWNER_SCOPE_MISMATCH'),
    ({'data_scope':'another-population'},'DATA_SCOPE_MISMATCH'),
])
def test_certificate_fails_closed_for_scope_and_identity(change,reason):
    c=certificate();c.update(change);r=validate(c)
    assert not r['eligible'] and reason in r['reasons']


@pytest.mark.parametrize('counts',[
    dict(correct=10,labelled=10,attempted=10,constraint_violations=0),
    dict(correct=2000,labelled=2000,attempted=2001,constraint_violations=0),
    dict(correct=2000,labelled=2000,attempted=2000,constraint_violations=1),
    dict(correct=True,labelled=1,attempted=1,constraint_violations=0),
])
def test_small_incomplete_unsafe_or_invalid_evidence_does_not_certify(counts):
    assert not validate(certificate({'c'*64:counts}))['eligible']


def test_multiple_cohorts_use_a_stricter_confidence_bound():
    c=certificate();one=validate(c)['cohorts']['c'*64]['precision_lower_bound']
    c['cohorts']['e'*64]=deepcopy(c['cohorts']['c'*64])
    assert validate(c)['cohorts']['c'*64]['precision_lower_bound']<one


def test_strict_shadow_component_is_read_only_and_bound_to_evidence():
    s=snapshot();before=deepcopy(s);r=policy().plan_components(s)
    assert len(r['components'])==1 and r['components'][0]['pair_ids']==['ab']
    assert s==before


@pytest.mark.parametrize('decision',['DEFERRED','REJECTED','NON_MATCH','ACCEPTED'])
def test_explicit_reviewer_intent_is_not_overwritten(decision):
    s=snapshot();s['overlays']['ab']=decision
    assert not policy().plan_components(s)['components']


def test_recovery_conflicts_and_shared_fields_stay_manual():
    for change in [dict(review_support={'kind':'NAME_FULL_DOB'}),dict(primary_group='D'),
                   dict(rule_conclusion='INSUFFICIENT_EVIDENCE')]:
        s=snapshot();s['pairs'][0].update(change)
        assert not policy().plan_components(s)['components']
    s=snapshot();s['pairs'][0]['comparisons']['phone']['state']='SH'
    assert not policy().plan_components(s)['components']


def test_absent_or_vetoed_triangle_edge_cannot_be_automatically_closed():
    s=snapshot(3);s['pairs']=[p for p in s['pairs'] if p['id']!='ac']
    assert not policy().plan_components(s)['components']
    s=snapshot(3);s['negative_pairs']=[('a','c')]
    r=policy().plan_components(s)
    assert all(not {'a','c'}<=set(c['record_ids']) for c in r['components'])


def test_component_cohort_binds_sources_and_complete_signature_set():
    s=snapshot();key=policy().component_cohort(s['pairs'],{r['id']:r for r in s['records']})
    s['records'][0]['source_system']='OTHER'
    assert policy().component_cohort(s['pairs'],{r['id']:r for r in s['records']})!=key


def test_two_member_validation_cannot_certify_a_larger_component_of_same_pattern():
    s=snapshot();by_id={r['id']:r for r in s['records']}
    key=policy().component_cohort(s['pairs'],by_id)
    extra=deepcopy(by_id['b']);extra['id']='c';by_id['c']=extra
    ac={**deepcopy(s['pairs'][0]),'id':'ac','right_id':'c'}
    bc={**deepcopy(s['pairs'][0]),'id':'bc','left_id':'b','right_id':'c'}
    assert policy().component_cohort([*s['pairs'],ac,bc],by_id)!=key
