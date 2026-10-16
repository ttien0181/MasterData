"""Same locked M0 formulas; marginal pass coverage, not invented unlabeled TN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))


def test_m2b_hand_metrics_incremental_recall_unknown_and_missing():
    from m2b_evaluation import evaluate_diagnostics
    from standalone.contracts.labels import PairLabel
    labels = {'P1': PairLabel.POSITIVE, 'P2': PairLabel.NEGATIVE, 'P3': PairLabel.POSITIVE,
              'P4': PairLabel.POSITIVE, 'P5': PairLabel.NEGATIVE, 'P6': PairLabel.UNKNOWN}
    decisions = {'P1': 'MATCH', 'P2': 'MATCH', 'P3': 'REVIEW', 'P5': 'NON_MATCH', 'P6': 'MATCH'}
    result = evaluate_diagnostics(labels, decisions, set(decisions),
                                  {'exact': {'P1', 'P2'}, 'embedding': {'P1', 'P3', 'P5', 'P6'}}, ['exact', 'embedding'], 6)
    metrics = result['classification']['binary_end_to_end']
    assert [metrics[k]['value'] for k in ('tp', 'fp', 'fn', 'tn')] == [1, 1, 2, 1]
    assert result['passes']['exact']['incremental_positive_pairs'] == 1
    assert result['passes']['embedding']['incremental_positive_pairs'] == 1
    assert result['passes']['embedding']['cumulative_positive_pairs'] == 2
    assert result['reduction_ratio'] == 1 - 5 / 15
    empty = evaluate_diagnostics({}, {}, set(), {}, [], 0)
    assert empty['candidate_recall'] is None and empty['reduction_ratio'] is None


def test_canonical_nonpositive_decisions_keep_locked_abstain_and_blocked_tracks():
    from m2b_evaluation import evaluate_diagnostics
    from standalone.contracts.labels import PairLabel
    labels = {'clear': PairLabel.POSITIVE, 'hard': PairLabel.NEGATIVE}
    result = evaluate_diagnostics(labels, {'clear': 'MERGEABLE', 'hard': 'NOT_MERGEABLE'},
                                  set(labels), {'rule': set(labels)}, ['rule'], 3)
    assert result['classification']['coverage']['abstain_rate_on_known_labels']['value'] == .5
    assert result['classification']['coverage']['decision_coverage_on_known_labels']['value'] == .5
