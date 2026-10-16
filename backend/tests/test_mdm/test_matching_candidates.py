"""Exact retrieval hand checks, duplicate postings, union and budget failure."""
from copy import deepcopy

import numpy as np
import pytest
from masterone.mdm.generation_staging import digest


def config():
    return {'encoder_identity': {'bundle_digest': 'test-bundle', 'dimension': 2},
            'preprocessing': {'version': 'unit-test'},
            'retrieval': {'query_batch_size': 64, 'target_batch_size': 256,
                          'embedding': {'top_k': 10, 'min_similarity': .85}},
            'execution_budget': {'similarity_cells': 100000000, 'workspace_bytes': 512 * 1024**2,
                                 'run_seconds': 1800, 'unique_pairs': 200000, 'warning_pairs': 150000}}


def bundle(vectors, cfg):
    return {field: {'vectors': vectors if field == 'name' else {},
                    'encoder_digest': cfg['encoder_identity']['bundle_digest'],
                    'preprocessing_digest': digest(cfg['preprocessing'])} for field in ('name', 'address')}


def records(count):
    return [{'id': f'r{i:04}', 'source_system': 'CRM' if i % 2 else 'ERP',
             'normalized_fields': {'name_unicode': 'name', '_field_quality': {'name': 'VALID'}}}
            for i in range(count)]


def test_tiled_topk_equals_exhaustive_record_level_duplicate_postings():
    from masterone.mdm.matching_agents.retrieval import retrieve_vectors
    rows, cfg = records(300), config()
    vectors = {row['id']: [1., 0.] if i < 270 else [.9, .2] for i, row in enumerate(rows)}
    result = retrieve_vectors(rows, bundle(vectors, cfg), cfg, lambda *_: None)
    actual = {(o['query_id'], o['target_id'], o['rank']) for o in result['observations']}
    ids = sorted(vectors)
    array = np.asarray([vectors[rid] for rid in ids], dtype=np.float32)
    array /= np.linalg.norm(array, axis=1, keepdims=True)
    scores = array @ array.T
    expected = set()
    for i, rid in enumerate(ids):
        neighbors = sorted([(float(scores[i, j]), target) for j, target in enumerate(ids)
                            if i != j and scores[i, j] >= .85], key=lambda item: (-item[0], item[1]))[:10]
        expected.update((rid, target, rank) for rank, (_, target) in enumerate(neighbors, 1))
    assert actual == expected
    assert result['fields']['name']['queried_records'] == 300
    assert result['fields']['name']['unique_vectors'] == 2
    assert result['similarity_cells'] == 4
    reverse = retrieve_vectors(list(reversed(rows)), bundle(dict(reversed(list(vectors.items()))), cfg), cfg, lambda *_: None)
    assert result['observations'] == reverse['observations']


@pytest.mark.parametrize('bad', [[0., 0.], [float('nan'), 1.], [float('inf'), 1.], [1.]])
def test_invalid_vector_never_retrieves(bad):
    from masterone.mdm.matching_agents.retrieval import retrieve_vectors
    cfg = config()
    with pytest.raises(ValueError, match='VECTOR'):
        retrieve_vectors(records(2), bundle({'r0000': bad, 'r0001': [1., 0.]}, cfg), cfg, lambda *_: None)


def test_identity_missing_coverage_budget_and_masks():
    from masterone.mdm.matching_agents.retrieval import retrieve_vectors
    cfg, rows = config(), records(2)
    data = bundle({'r0000': [1., 0.], 'r0001': [1., 0.]}, cfg)
    wrong = deepcopy(data)
    wrong['name']['encoder_digest'] = 'other'
    with pytest.raises(ValueError, match='IDENTITY'):
        retrieve_vectors(rows, wrong, cfg, lambda *_: None)
    with pytest.raises(ValueError, match='COVERAGE'):
        retrieve_vectors(rows, bundle({'r0000': [1., 0.]}, cfg), cfg, lambda *_: None)
    rows[1]['normalized_fields']['_field_quality']['name'] = 'PLACEHOLDER'
    result = retrieve_vectors(rows, bundle({'r0000': [1., 0.]}, cfg), cfg, lambda *_: None)
    assert result['observations'] == []
    assert result['fields']['address']['eligible_success_ratio'] is None
    rows = records(2)
    cfg['execution_budget']['similarity_cells'] = 1
    with pytest.raises(ValueError, match='BUDGET'):
        retrieve_vectors(rows, bundle({'r0000': [1., 0.], 'r0001': [0., 1.]}, cfg), cfg, lambda *_: None)


def test_union_retains_legacy_historical_and_all_directions():
    from masterone.mdm.matching_agents.candidate_union import build_union
    from masterone.mdm.pipeline.candidate_engine import CandidateEngine
    rows, cfg = records(3), config()
    cfg['experiment'] = 'E3'
    cfg['retrieval']['tfidf'] = {'top_k': 10, 'min_similarity': .30}
    vectors = bundle({r['id']: [1., 0.] for r in rows}, cfg)
    result = build_union(rows, cfg, vectors, [], lambda *_: None)
    assert set(CandidateEngine().generate(rows).pairs) <= set(result.pairs)
    assert len(result.pairs) == 3
    assert result.metrics['unique_candidates'] == 3
    assert result.metrics['passes']['embedding_name']['directed_observations'] == 6
    assert sum(p['new_unique'] for p in result.metrics['passes'].values()) == result.metrics['unique_candidates']
    assert len([o for o in result.metrics['provenance'] if o['pass'] == 'embedding_name']) == 6
    cfg['experiment'] = 'E1'
    result = build_union(rows, cfg, None, [{'id': 'historical', 'left_id': 'r0000', 'right_id': 'r0002'}], lambda *_: None)
    assert ('r0000', 'r0002') in result.pairs
    assert 'retained_history' in result.pairs['r0000', 'r0002']
    assert result.metrics['retrieved_current'] == 0


def test_oversized_fallback_hub_and_union_budget_failure():
    from masterone.mdm.matching_agents.candidate_union import build_union
    rows, cfg = records(21), config()
    cfg['experiment'] = 'E1'
    cfg['retrieval']['tfidf'] = {'top_k': 10, 'min_similarity': .30}
    for r in rows:
        r['normalized_fields'].update(name='nguyen van an', name_unicode='nguyễn văn an',
                                      phones=[{'value': 'synthetic-shared-contact', 'quality': 'VALID', 'shared': True}])
    result = build_union(rows, cfg, None, [], lambda *_: None)
    recovered = [o for o in result.metrics['provenance'] if o['pass'] == 'tfidf_fallback_name']
    assert recovered and all(o['fallback_blocks'] for o in recovered)
    assert result.metrics['fallback']['oversized_blocks'] > 0
    assert result.metrics['fallback']['fields']['address']['available_records'] == 0
    assert result.metrics['fallback']['fields']['address']['unavailable_reason'] == 'NO_ELIGIBLE_TEXT'
    cfg['execution_budget']['unique_pairs'] = 1
    with pytest.raises(ValueError, match='CANDIDATE_BUDGET'):
        build_union(rows, cfg, None, [], lambda *_: None)


def test_union_handoff_scores_each_unique_pair_once_in_production(tmp_path):
    from masterone.mdm.matching_agents.candidate_union import build_union
    from masterone.mdm.pipeline.standardization import standardize
    from masterone.mdm.resolution_service import _compute
    cfg, rows = config(), records(3)
    cfg['experiment'] = 'E3'
    cfg['retrieval']['tfidf'] = {'top_k': 10, 'min_similarity': .30}
    for r in rows:
        r['raw_fields'] = {'name': 'Nguyễn Văn An', 'phone': '0901234567', 'address': '12 phố Huế Hà Nội'}
        r['entity_type'] = 'PERSON'
        r['normalized_fields'], r['quality_flags'] = standardize(r['raw_fields'], 'PERSON')
    vectors = bundle({r['id']: [1., 0.] for r in rows}, cfg)
    vectors['address']['vectors'] = {r['id']: [1., 0.] for r in rows}
    union = build_union(rows, cfg, vectors, [], lambda *_: None)
    result = _compute(rows, 'test-run', 'person-v3', scoring_output_dir=tmp_path,
                      integration_candidates=union, _stop_after_decide=True)
    assert result['scoring_metrics']['input_pairs'] == result['scoring_metrics']['scored_pairs'] == len(union.pairs) == 3
    assert result['scoring_metrics']['model_version'] == 'p119_rule_v1'
    assert result['candidate_count'] == 3


def test_exact_targets_across_unique_vector_tiles():
    from masterone.mdm.matching_agents.retrieval import retrieve_vectors
    rows, cfg = records(300), config()
    cfg['encoder_identity']['dimension'] = 300
    # Orthogonal vectors plus a duplicate at the last target tile; exact cosine0 excludes every unrelated neighbor.
    vectors = {r['id']: np.eye(300, dtype=np.float32)[i].tolist() for i, r in enumerate(rows)}
    vectors['r0299'] = vectors['r0000']
    result = retrieve_vectors(rows, bundle(vectors, cfg), cfg, lambda *_: None)
    assert {(o['query_id'], o['target_id']) for o in result['observations']} == {('r0000', 'r0299'), ('r0299', 'r0000')}
    assert result['fields']['name']['unique_vectors'] == 299


def test_execution_budget_guards_memory_disk_time_and_scoring_env(tmp_path, monkeypatch):
    from masterone.mdm.matching_agents.execution_budget import check_resources, scoring_scope
    cfg = {'resource_budget': {'worker_rss_bytes': 100},
           'execution_budget': {'run_seconds': 1, 'disk_bytes': 100, 'rule_workers': 1}}
    with pytest.raises(ValueError, match='RSS'):
        check_resources(cfg, elapsed=0, rss=101, output_bytes=0)
    with pytest.raises(ValueError, match='DISK'):
        check_resources(cfg, elapsed=0, rss=10, output_bytes=101)
    with pytest.raises(ValueError, match='TIMEOUT'):
        check_resources(cfg, elapsed=2, rss=10, output_bytes=10)
    import os
    monkeypatch.setenv('MDM_SCORE_WORKERS', '4')
    with scoring_scope(cfg):
        assert os.environ['MDM_SCORE_WORKERS'] == '1'
    assert os.environ['MDM_SCORE_WORKERS'] == '4'
