from copy import deepcopy
from itertools import combinations

import pytest
from masterone.mdm.engine_adapter import engine_context
from masterone.mdm.pipeline.matching import value_frequencies
from masterone.mdm.pipeline.pair_assessment import assess_pair
from masterone.mdm.workflow_finalize import compute_publication

from tests.test_mdm.test_pair_classification_regressions import record

PROFILE = dict(decision_semantics_v2_enabled=True, cluster_safety_v2_enabled=True, generation_staging_enabled=False)


def source():
    records = [record(rid, phone='0912345678', email=('first@example.test' if rid < 'd' else 'second@example.test'))
               for rid in 'abcdef']
    frequencies = value_frequencies(records)
    pairs = [{**assess_pair(a, b, frequencies), 'id': a['id'] + b['id']}
             for group in (records[:3], records[3:]) for a, b in combinations(group, 2)]
    return dict(records=records, pairs=pairs, overlays={p['id']: 'ACCEPTED' for p in pairs},
                review_reasons={}, negative_pairs=[], profile=PROFILE, policy_version='person-v3')


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('decision', ['REJECTED', 'DEFERRED', 'ACCEPTED'])
def test_changed_region_matches_full_compute_and_reuses_disconnected_groups(partial, decision):
    original = source()
    original['allow_partial'] = partial
    with engine_context(PROFILE):
        previous = compute_publication(original, 'r', 1)
        changed = deepcopy(original)
        changed['overlays']['ab'] = decision
        actual = compute_publication(changed, 'r', 2, previous_plan={'snapshot': original, 'computed': previous})
        full = compute_publication(changed, 'r', 2)
    assert actual['clusters'] == full['clusters']
    assert actual['publication'] == full['publication']
    assert actual['conflicts'] == full['conflicts']
    assert actual['pairs'] == full['pairs']
    assert actual['region_cache']['reused_records'] >= 3


def test_changed_inputs_force_full_validation_instead_of_reusing_regions():
    original = source()
    with engine_context(PROFILE):
        previous = compute_publication(original, 'r', 1)
        changed = deepcopy(original)
        changed['records'][0]['normalized_fields']['phone'] = '0999999999'
        actual = compute_publication(changed, 'r', 2, previous_plan={'snapshot': original, 'computed': previous})
        full = compute_publication(changed, 'r', 2)
    assert actual['clusters'] == full['clusters']
    assert actual['region_cache']['reused_records'] == 0


def test_unchecked_pending_regions_use_cheap_full_compute_instead_of_copying_cache():
    original = source()
    original.update(allow_partial=True, overlays={})
    with engine_context(PROFILE):
        previous = compute_publication(original, 'r', 1)
        assert previous['cluster_metrics']['total_checked'] == 0
        changed = deepcopy(original)
        changed['overlays']['ab'] = 'REJECTED'
        actual = compute_publication(changed, 'r', 2, previous_plan={'snapshot': original, 'computed': previous})
        assert actual['region_cache']['reused_records'] == 0
