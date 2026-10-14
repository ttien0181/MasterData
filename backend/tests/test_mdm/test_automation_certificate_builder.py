"""Certification counts whole disjoint components from actual ownership labels."""
import importlib.util
from pathlib import Path
import pytest


def builder():
    path=Path(__file__).resolve().parents[3]/'scripts/pair_automation_certificate.py'
    spec=importlib.util.spec_from_file_location('certificate_builder',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def shadow():
    return dict(code_identity='a'*64,profile_identity='b'*64,owner_user_id='owner',source_systems=['CRM','ERP'],
        components=[dict(record_ids=['a','b'],cohort='c'*64),dict(record_ids=['c','d'],cohort='c'*64)])


def test_component_validation_counts_false_merges_and_missing_labels():
    s=shadow();c=builder().build_certificate([s],{'a':'E1','b':'E1','c':'E2'},
        data_scope='scope',scope='synthetic-benchmark',independent=False,representative=False,expires_at='2099-01-01T00:00:00Z',validation_hash='d'*64)
    assert c['cohorts']['c'*64]==dict(correct=1,labelled=1,attempted=2,constraint_violations=0)
    c=builder().build_certificate([s],{'a':'E1','b':'E1','c':'E2','d':'E3'},
        data_scope='scope',scope='production',independent=True,representative=True,expires_at='2099-01-01T00:00:00Z',validation_hash='d'*64)
    assert c['cohorts']['c'*64]['correct']==1 and c['cohorts']['c'*64]['labelled']==2


def test_duplicate_component_samples_cannot_inflate_validation_size():
    with pytest.raises(ValueError,match='overlap'):
        builder().build_certificate([shadow(),shadow()],{},data_scope='scope',scope='production',independent=True,
            representative=True,expires_at='2099-01-01T00:00:00Z',validation_hash='d'*64)


def test_mixed_code_profile_or_owner_cannot_be_pooled():
    for field in ['code_identity','profile_identity','owner_user_id']:
        a,b=shadow(),shadow();b[field]='other'
        with pytest.raises(ValueError,match='identity'):
            builder().build_certificate([a,b],{},data_scope='scope',scope='production',independent=True,
                representative=True,expires_at='2099-01-01T00:00:00Z',validation_hash='d'*64)
