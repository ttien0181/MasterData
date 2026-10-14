"""Publication rechecks accepted review support, including every direct bridge."""
from contextlib import closing

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.cluster_guards import check_component
from masterone.mdm.pipeline.cluster_policy import ClusterPolicy, GuardBudget
from masterone.mdm.pipeline.decision_store import DecisionStore
from masterone.mdm.pipeline.matching import value_frequencies
from masterone.mdm.pipeline.pair_assessment import assess_pair, assessment_profile
from tests.test_mdm.test_pair_classification_regressions import record


def recovered(a,b,decision='ACCEPTED',reason=None):
    return {**assess_pair(a,b),'id':a['id']+':'+b['id'],'user_decision':decision,'review_reason':reason}


def check(records,pairs,links=frozenset()):
    with engine_context(assessment_profile()), closing(DecisionStore()) as store:
        for pair in pairs:store.add(pair)
        store.seal()
        return check_component({r['id'] for r in records},{r['id']:r for r in records},store,
                               ClusterPolicy(),GuardBudget(),value_frequencies(records),links)


def pair_records():
    return record('a',phone='',email='',address=''),record('b',phone='',email='',address='')


def test_explicit_acceptance_of_current_support_can_publish_without_contact():
    a,b=pair_records()
    assert check([a,b],[recovered(a,b)]) == ''


def test_unaccepted_support_does_not_grant_group_permission():
    a,b=pair_records()
    assert check([a,b],[recovered(a,b,decision='UNDECIDED')]) == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'


def test_changed_dob_does_not_inherit_cached_support():
    a,b=pair_records();saved=recovered(a,b)
    b=record('b',phone='',email='',address='',dob='1981-01-01')
    assert check([a,b],[saved]) == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'


def test_forged_support_marker_is_not_identity_evidence():
    a,b=pair_records()
    b=record('b',name='Tran Thi Binh',phone='',email='',address='',dob='1981-01-01')
    forged=recovered(a,b)
    forged.update(review_support={'policy_version':'person-review-support-v1','kind':'NAME_FULL_DOB','fields':['name','dob']})
    assert check([a,b],[forged]) == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'


def test_missing_unaccepted_bridge_still_blocks_transitive_closure():
    a,b=pair_records();c=record('c',phone='',email='',address='')
    assert check([a,b,c],[recovered(a,b),recovered(b,c)]) == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'
    assert check([a,b,c],[recovered(a,b),recovered(b,c),recovered(a,c)]) == ''


def test_conflicting_name_still_requires_reason_after_support_recovery():
    a=record('a',phone='',email='');b=record('b',name='Tran Thi Binh',phone='',email='')
    assert check([a,b],[recovered(a,b)]) == 'SOFT_ACCEPT_REASON_REQUIRED'
    assert check([a,b],[recovered(a,b,reason='Documents confirm the name change')]) == ''


def test_verified_conflict_and_negative_links_outrank_support():
    a,b=pair_records();saved=recovered(a,b)
    a=record('a',phone='',email='',address='',dob_verified=True)
    b=record('b',phone='',email='',address='',dob='1981-01-01',dob_verified=True)
    assert check([a,b],[saved]) == 'VERIFIED_DOB_CONFLICT'
    a,b=pair_records()
    assert check([a,b],[recovered(a,b)],frozenset({('a','b')})) == 'CLUSTER_CONFIRMED_CANNOT_LINK'
