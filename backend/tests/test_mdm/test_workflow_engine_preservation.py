"""Canonical outputs captured before workflow implementation; no live providers."""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.clustering import build_proposals
from masterone.mdm.pipeline.golden import build_golden
from masterone.mdm.resolution_service import _compute

from tests.test_mdm.test_decision_semantics import record
from tests.test_mdm.test_safe_clustering import chain, edge

BASE = Path(__file__).parent / 'fixtures' / 'workflow_baseline.json'


def canonical_baseline(tmp_path):
    result = {}
    for v2 in (False, True):
        profile = dict(decision_semantics_v2_enabled=v2, cluster_safety_v2_enabled=v2,
                       generation_staging_enabled=False)
        with engine_context(profile):
            rows = [record('a'), record('b'), record('singleton', name='Other', phone='0987654321', email='other@example.test')]
            computed = _compute(rows, 'fixed-run', 'person-v3', scoring_output_dir=tmp_path / str(v2))
            result[str(v2)] = {k: deepcopy(computed[k]) for k in ('proposals', 'candidate_count', 'insufficient_count', 'candidate_metrics')}
            result[str(v2)]['pairs'] = list(computed['pairs'])
            if hasattr(computed['pairs'], 'close'):
                computed['pairs'].close()
            a, b, c = chain()
            for kind in ('bridge', 'veto', 'cannot'):
                pairs = [edge(a, b, 15), edge(b, c, 14)]
                clusters = build_proposals([a, b, c], pairs, 'fixed-run', 'person-v3',
                    negative_pairs=frozenset({('a', 'c')}) if kind == 'cannot' else None,
                    execution_blocked_pairs=frozenset({('a', 'c')}) if kind == 'veto' else frozenset())
                result[str(v2)][kind] = {'clusters': clusters, 'pairs': pairs}
            a, b = record('a', name='First'), record('b', name='Second')
            a['source_system'], b['source_system'] = 'BILL', 'CRM'
            a['normalized_fields']['_name_verified'] = True
            result[str(v2)]['golden'] = build_golden(['b', 'a'], {r['id']: r for r in (a, b)})
            result[str(v2)]['manual'] = build_golden(['b', 'a'], {r['id']: r for r in (a, b)}, existing_golden={'_golden_override': {'name': 'Manual'}})
    return json.loads(json.dumps(result, sort_keys=True, default=list))


def test_workflow_engine_preservation(tmp_path, monkeypatch):
    from masterone.config import get_settings
    monkeypatch.setattr(get_settings(), 'semantic_address_enabled', False)
    monkeypatch.setattr(get_settings(), 'llm_enabled', False)
    actual = canonical_baseline(tmp_path)
    expected = json.loads(BASE.read_text(encoding='utf-8'))
    # Keep the historical fixture immutable. Preserve matching/groups and unrelated
    # guards; verify the intentionally changed golden contract independently.
    for profile in ('False', 'True'):
        assert actual[profile]['golden']['fields']['name']['value'] == 'First'
        assert actual[profile]['manual']['fields']['name']['value'] == 'Manual'
        for key in ('golden', 'manual'):
            actual[profile].pop(key)
            expected[profile].pop(key)
    from masterone.mdm.pipeline.scoring_engine import MemorySnapshot, ScoringEngine
    current_hash = ScoringEngine(MemorySnapshot([])).rule_hash
    for profile in ('False', 'True'):
        for pair in actual[profile]['pairs']:
            assert pair.pop('scoring_model_hash') == current_hash
        for pair in expected[profile]['pairs']:
            pair.pop('scoring_model_hash')
    def project_historical_contract(value):
        if isinstance(value, list):
            for item in value:
                project_historical_contract(item)
        elif isinstance(value, dict):
            value.pop('golden_fields', None)
            if 'survivorship_version' in value:
                assert value.pop('survivorship_version') == 'p119_golden_v3'
                assert value.pop('field_source_priority') == []
                assert value.pop('max_reviewed_cluster_size') == 100
                assert value.pop('max_reviewed_pair_checks_per_union') == 4950
            if {'visited', 'checked', 'cache_hits'} <= value.keys():
                # Cache hits no longer spend the expensive guard-check budget.
                # Preserve the old metric projection while checking this change.
                assert value['visited'] == value['checked']
                value['visited'] += value['cache_hits']
            if 'excluded_records_by_source' in value:
                # These fixture records have no DOB. The new bounded passes
                # add exclusion provenance, without changing any historical pair.
                excluded=value['excluded_records_by_source']
                for source in ('name_full_dob','dob_numbered_street'):
                    assert excluded.pop(source)==value['record_count']
            if 'conflict_rule_version' in value:
                assert value['conflict_rule_version'] == 'v3.2.0'
                value['conflict_rule_version'] = 'v3.1.0'
            if value.get('policy_version') == 'person-pair-classification-v2':
                value['policy_version'] = 'person-linh-v2-selective'
            if 'unverified_identifier_relation' in value:
                assert value.pop('unverified_identifier_relation') == 'NOT_COMPARABLE'
            if value.get('field') in ('phone', 'email') and 'matched_values' in value:
                assert isinstance(value.pop('matched_values'), list)
                frequency = value.pop('matched_frequency')
                assert frequency is None or frequency == 2
            for item in value.values():
                project_historical_contract(item)
    project_historical_contract(actual)
    # Project only the known DF/shared-warning fix from the frozen old fixture.
    def remove_golden_fields(value):
        if isinstance(value, dict):
            value.pop('golden_fields', None)
            if value.get('guard_violations'):
                states = {value.get('comparisons', {}).get(f, {}).get('state') for f in ('phone', 'email')}
                if 'SH' not in states:
                    value['guard_violations'] = [v for v in value['guard_violations']
                                                if v['reason'] != 'CLUSTER_SHARED_VALUE_DOMINANT']
            for item in value.values():
                remove_golden_fields(item)
        elif isinstance(value, list):
            for item in value:
                remove_golden_fields(item)
    remove_golden_fields(expected)
    assert actual == expected
    assert sha256(BASE.read_bytes()).hexdigest() == BASE.with_suffix('.sha256').read_text().strip()
