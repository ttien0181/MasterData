"""Quality denominators include blocking misses and uncertain label coverage."""
import importlib.util
from pathlib import Path

import pytest


def verifier():
    path=Path(__file__).resolve().parents[3]/'scripts/verify_pair_quality.py'
    spec=importlib.util.spec_from_file_location('pair_quality_verifier',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_retrieval_and_review_denominators_include_unretrieved_true_pairs():
    m=verifier().review_metrics({'a','b','c','d'},{'a':'E1','b':'E1','c':'E1','d':'E2'},
        {('a','b'),('a','d')},[
            {'left_id':'a','right_id':'b','decision':'REVIEW','required':True,'review_support':{'kind':'NAME_FULL_DOB'}},
            {'left_id':'a','right_id':'d','decision':'INSUFFICIENT_EVIDENCE','required':False}])
    assert m['known_positive_pairs'] == 3
    assert m['retrieved_positive_pairs'] == m['required_positive_pairs'] == 1
    assert m['blocking_missed_positive_pairs'] == 2
    assert m['retrieval_recall'] == m['review_capture_recall'] == pytest.approx(1/3)
    assert m['recovered_by_kind']['NAME_FULL_DOB']['true_positive'] == 1


def test_transitive_false_merges_are_counted_in_cluster_pair_quality():
    m=verifier().cluster_metrics({'a','b','c','d'},{'a':'E1','b':'E1','c':'E2','d':'E1'},[['a','b','c']])
    assert m['cluster_pair_true_positive'] == 1 and m['cluster_pair_false_positive'] == 2
    assert m['cluster_pair_precision'] == pytest.approx(1/3)
    assert m['cluster_pair_recall'] == pytest.approx(1/3)
    assert m['attempted_multi_record_components'] == 1 and m['false_merge_components'] == 1


def test_unlabelled_component_cannot_be_counted_as_correct():
    m=verifier().cluster_metrics({'a','b'},{'a':'E1'},[['a','b']])
    assert m['attempted_multi_record_components'] == 1
    assert m['labelled_multi_record_components'] == m['correct_multi_record_components'] == 0
    assert m['cluster_pair_precision'] is None


def test_zero_attempts_and_small_perfect_samples_are_not_certified():
    v=verifier()
    assert not v.automation_readiness(0,0,0,scope='production',independent_holdout=True,representative=True)['eligible']
    small=v.automation_readiness(10,10,10,scope='production',independent_holdout=True,representative=True)
    assert not small['eligible'] and small['precision_lower_bound'] < .995


def test_synthetic_or_explored_labels_do_not_authorize_production_automation():
    v=verifier()
    good=v.automation_readiness(1000,1000,1000,scope='production',independent_holdout=True,representative=True)
    assert good['eligible'] and good['precision_target'] == .995
    bad=v.automation_readiness(1000,1000,1000,scope='synthetic-benchmark',independent_holdout=False,representative=False)
    assert not bad['eligible']
    assert {'SYNTHETIC_ONLY','NO_INDEPENDENT_HOLDOUT','NON_REPRESENTATIVE_LABELS'} <= set(bad['reasons'])


def test_missing_labels_constraints_and_duplicate_memberships_fail_closed():
    v=verifier()
    g=v.automation_readiness(1000,1000,1001,scope='production',independent_holdout=True,representative=True,constraint_violations=1)
    assert not g['eligible'] and {'INCOMPLETE_LABEL_COVERAGE','CONSTRAINT_VIOLATION'} <= set(g['reasons'])
    with pytest.raises(ValueError,match='membership'):
        v.cluster_metrics({'a','b'},{'a':'E1','b':'E1'},[['a','b'],['a']])
    with pytest.raises(ValueError,match='membership'):
        v.cluster_metrics({'a','b'},{'a':'E1','b':'E1'},[['a','a','b']])


def test_missing_owner_value_is_not_a_known_identity():
    m=verifier().cluster_metrics({'a','b'},{'a':'E1','b':None},[['a','b']])
    assert m['labelled_multi_record_components']==0
    assert m['false_merge_components']==0
    assert m['cluster_pair_precision'] is None


def test_person_scope_excludes_organization_components_without_hiding_mixed_merges():
    v=verifier()
    m=v.scoped_cluster_metrics({'a','b'}, {'a','b','org'}, {'a':'E1','b':'E1'}, [['a','b'],['org']])
    assert m['correct_multi_record_components']==1
    assert m['singleton_count']==0
    with pytest.raises(ValueError,match='scope'):
        v.scoped_cluster_metrics({'a','b'}, {'a','b','org'}, {}, [['a','org']])
    with pytest.raises(ValueError,match='membership'):
        v.scoped_cluster_metrics({'a','b'}, {'a','b','org'}, {}, [['org'],['org']])


def test_blank_raw_entity_type_matches_runtime_unknown_person_scope():
    assert verifier().person_scope([
        {'id':'a','entity_type':'PERSON'},
        {'id':'b','entity_type':'','normalized_fields':{}},
        {'id':'c','entity_type':None,'normalized_fields':{}},
        {'id':'org','entity_type':'ORGANIZATION'},
    ]) == {'a','b','c'}


def test_source_identity_tracks_code_changes_but_not_checkout_location(tmp_path):
    v=verifier()
    roots=[tmp_path/'before',tmp_path/'after']
    for root in roots:
        p=root/'masterone/mdm/pipeline/candidate_engine.py'
        p.parent.mkdir(parents=True)
        p.write_text('PASS_LIMIT = 20\n')
    assert v.source_code_identity(roots[0]) == v.source_code_identity(roots[1])
    (roots[1]/'masterone/mdm/pipeline/candidate_engine.py').write_text('PASS_LIMIT = 30\n')
    assert v.source_code_identity(roots[0])['digest'] != v.source_code_identity(roots[1])['digest']
