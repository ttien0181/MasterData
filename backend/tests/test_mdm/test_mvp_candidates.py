"""Opt-in Step 2 candidate conditioning contracts."""
import pytest

from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
from masterone.mdm.pipeline.candidate_planner import WorkloadPlanError, WorkloadPolicy


def record(rid, **fields):
    return {'id': rid, 'normalized_fields': fields}


def run(records, **kwargs):
    return CandidateEngine(CandidateConfig(mvp_step2=True, include_tfidf=False, recall_v2=False, **kwargs)).generate(records)


def test_common_name_and_address_noise_is_removed_without_changing_legacy():
    records = [record(str(i), name='nguyen van an', address='ha noi', dob=f'1990-01-{i+1:02}') for i in range(8)]
    assert len(CandidateEngine(CandidateConfig(include_tfidf=False, recall_v2=False)).generate(records).pairs) == 28
    result = run(records)
    assert result.pairs == {}
    assert result.metrics['conditional_candidates']['rejected_pair_observations'] > 28
    assert result.metrics['conditional_candidates']['unique_pairs_after_gate'] == 0
    assert all(log['generated_pairs'] == 0 for log in result.block_log)


@pytest.mark.parametrize('evidence', [
    {'dob': '1990-01-02', '_field_quality': {'dob': 'VALID'}},
    {'phones': [{'value': '0901234567', 'quality': 'VALID'}]},
    {'emails': [{'value': 'an@example.com', 'quality': 'VALID'}]},
    {'government_id': '012345678901', '_field_quality': {'government_id': 'VALID'}},
    {'address': '12 nguyen trai, ha noi'},
])
def test_secondary_evidence_retains_canonical_pair_and_all_passing_sources(evidence):
    result = run([record('z', name='nguyen van an', **evidence), record('a', name='nguyen van an', **evidence)])
    assert ('a', 'z') in result.pairs
    assert {'exact_name', 'name_gram'} <= result.pairs[('a', 'z')]
    assert result.metrics['candidate_count'] == 1


def test_invalid_dob_and_administrative_address_are_not_secondary_support():
    result = run([record(rid, name='nguyen van an', dob='1990-01-02', address='12 phuong trung hoa',
                         _field_quality={'dob': 'INVALID'}) for rid in ('a', 'z')])
    assert result.pairs == {}


def test_month_day_two_name_tokens_recovery_is_retained():
    records = [record('a', name='nguyen van an', dob='1990-01-02', _field_quality={'name': 'VALID', 'dob': 'VALID'}),
               record('z', name='nguyen van anh', dob='1991-01-02', _field_quality={'name': 'VALID', 'dob': 'VALID'})]
    result = CandidateEngine(CandidateConfig(mvp_step2=True, include_tfidf=False, recall_v2=True)).generate(records)
    assert {'person_tokens_month_day', 'name_gram'} <= result.pairs[('a', 'z')]


def test_shared_or_historical_multivalue_contact_does_not_corroborate_weak_sources():
    for marker in ('shared', 'shared_risk', 'historical', 'origin_id'):
        records = [record(rid, name='nguyen van an', phones=[{'value': '0901234567', 'quality': 'VALID', marker: True}])
                   for rid in ('a', 'z')]
        result = run(records)
        assert result.pairs[('a', 'z')] == {'exact_phone'}
    result = run([record('a', name='nguyen van an', phones=[{'value': '1', 'quality': 'INVALID'}, {'value': '0901234567', 'quality': 'VALID'}]),
                  record('z', name='nguyen van an', phones=[{'value': '0901234567', 'quality': 'VALID'}])])
    assert 'exact_name' in result.pairs[('a', 'z')]


@pytest.mark.parametrize('sparse', [False, True])
def test_tfidf_cannot_reintroduce_rejected_weak_pairs(sparse):
    # Real legacy TF-IDF emits this exact-text pair; the union must still enforce conditioning.
    records = [record(rid, name='nguyen van an') for rid in ('a', 'z')]
    result = CandidateEngine(CandidateConfig(mvp_step2=True, sparse_tfidf=sparse, recall_v2=False)).generate(records)
    assert result.pairs == {}
    assert result.metrics['conditional_candidates']['tfidf_partitions']['searched_pools'] == 0


def test_exact_anchor_survives_when_all_secondary_fields_are_missing():
    result = run([record(rid, phones=[{'value': '0901234567', 'quality': 'VALID'}]) for rid in ('a', 'z')])
    assert result.pairs == {('a', 'z'): {'exact_phone'}}


def test_frequent_contacts_are_anchors_but_cannot_corroborate_common_names():
    result = run([record(str(i), name='nguyen van an',
                         phones=[{'value': '0901234567', 'quality': 'VALID'}]) for i in range(6)])
    assert len(result.pairs) == 15
    assert all(sources == {'exact_phone'} for sources in result.pairs.values())


def test_raw_shared_risk_cannot_corroborate_weak_sources():
    records = [record(rid, name='nguyen van an', phones=[{'value': '0901234567', 'quality': 'VALID'}])
               for rid in ('a', 'z')]
    records[0]['raw_fields'] = {'phone_shared_risk': 'true'}
    assert run(records).pairs == {('a', 'z'): {'exact_phone'}}


def test_mvp_retained_evidence_is_isolated_from_caller_mutation():
    records = [record(rid, name='nguyen van an', dob='1990-01-02', _field_quality={'dob': 'VALID'})
               for rid in ('a', 'z')]
    engine = CandidateEngine(CandidateConfig(mvp_step2=True, include_tfidf=False, recall_v2=False))
    engine.add_records(records)
    records[0]['normalized_fields']['_field_quality']['dob'] = 'INVALID'
    records[0]['normalized_fields']['dob'] = '1991-01-02'
    assert 'exact_name' in engine.finalize().pairs[('a', 'z')]


@pytest.mark.parametrize('sparse', [False, True])
def test_unsupported_tfidf_neighbors_cannot_consume_supported_neighbors_top_k(sparse):
    records = [record('a', name='nguyen van an', dob='1990-01-02', _field_quality={'dob': 'VALID'}),
               record('z', name='nguyen van anh', dob='1990-01-02', _field_quality={'dob': 'VALID'})]
    for i in range(25):
        for name in ('nguyen van an', 'nguyen van anh'):
            records.append(record(f'{i}-{name}', name=name, dob=f'1990-02-{i+1:02}',
                                  _field_quality={'dob': 'VALID'}))
    result = CandidateEngine(CandidateConfig(mvp_step2=True, sparse_tfidf=sparse, recall_v2=False,
                                            composites=(), include_grams=False, name_block_size=1)).generate(records)
    assert result.pairs[('a', 'z')] == {'tfidf_name'}


@pytest.mark.parametrize('sparse', [False, True])
def test_tfidf_workload_counts_only_supported_pool_search_cells(sparse):
    records = [record('a', name='nguyen van an', dob='1990-01-02', _field_quality={'dob': 'VALID'}),
               record('z', name='nguyen van anh', dob='1990-01-02', _field_quality={'dob': 'VALID'}),
               record('other', name='tran thi hoa')]
    result = CandidateEngine(CandidateConfig(mvp_step2=True, sparse_tfidf=sparse, recall_v2=False, composites=(),
                                            include_grams=False, workload=WorkloadPolicy(max_similarity_cells=4))).generate(records)
    assert result.pairs[('a', 'z')] == {'tfidf_name'}
    assert result.metrics['tfidf']['name']['estimated_similarity_cells'] == 4


@pytest.mark.parametrize('policy', [WorkloadPolicy(max_similarity_cells=3), WorkloadPolicy(memory_budget_bytes=1),
                                    WorkloadPolicy(max_dot_products=1)])
def test_supported_tfidf_search_still_enforces_workload_guards(policy):
    records = [record(rid, name='nguyen van an', dob='1990-01-02', _field_quality={'dob': 'VALID'})
               for rid in ('a', 'z')]
    with pytest.raises(WorkloadPlanError):
        CandidateEngine(CandidateConfig(mvp_step2=True, recall_v2=False, workload=policy)).generate(records)


def test_oversized_supported_tfidf_pool_is_skipped_and_counted_as_observations():
    records = [record(str(i), name='nguyen van an', dob='1990-01-02', _field_quality={'dob': 'VALID'})
               for i in range(4)]
    result = CandidateEngine(CandidateConfig(mvp_step2=True, recall_v2=False, max_block_size=3,
                                            include_grams=False, composites=())).generate(records)
    assert result.pairs == {}
    assert result.metrics['conditional_candidates']['tfidf_partitions']['oversized_pools'] == 1
    assert result.metrics['conditional_candidates']['tfidf_partitions']['oversized_pool_pair_observations'] == 6
