"""New passes recover bounded cohorts that broad legacy buckets skip."""
from datetime import date, timedelta

from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
from tests.test_mdm.test_pair_classification_regressions import record


def test_common_name_bucket_still_finds_a_specific_full_dob_pair():
    records=[record(str(i),phone='',email='',address='',dob=(date(1980,1,1)+timedelta(days=max(0,i-1))).isoformat()) for i in range(70)]
    result=CandidateEngine(CandidateConfig(include_tfidf=False,include_grams=False)).generate(records)
    assert ('0','1') in result.pairs
    assert 'name_full_dob' in result.pairs['0','1']
    assert len(result.pairs) == 1


def test_numbered_street_dob_pass_handles_different_names_and_admin_suffixes():
    a=record('a',phone='',email='',address='12 Nguyen Trai, Phuong 7, Ha Noi')
    b=record('b',name='Tran Thi Binh',phone='',email='',address='12 Nguyen Trai, Phuong 8, Ha Noi')
    result=CandidateEngine(CandidateConfig(include_tfidf=False,include_grams=False)).generate([a,b])
    assert 'dob_numbered_street' in result.pairs['a','b']


def test_partial_birth_date_and_generic_ward_do_not_generate_new_composites():
    a=record('a',phone='',email='',dob='1990',address='Phuong 7, Ha Noi')
    b=record('b',phone='',email='',dob='1990',address='Phuong 7, Ha Noi')
    result=CandidateEngine(CandidateConfig(include_tfidf=False,include_grams=False)).generate([a,b])
    assert not {'name_full_dob','dob_numbered_street'}.intersection(result.pairs.get(('a','b'),set()))


def test_numbered_street_pass_keeps_its_own_bucket_limit():
    records=[record(str(i),name=f'Person Family Given{i}',phone='',email='') for i in range(21)]
    result=CandidateEngine(CandidateConfig(include_tfidf=False,include_grams=False)).generate(records)
    assert all('dob_numbered_street' not in sources for sources in result.pairs.values())
