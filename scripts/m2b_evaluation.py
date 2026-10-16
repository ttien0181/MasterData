"""Locked diagnostic formula adapter; only known labels, explicit pass order."""
from m0_evaluation import evaluate_pairs
from standalone.contracts.labels import PairLabel


def evaluate_diagnostics(labels, decisions, candidates, pass_ids, pass_order, record_count):
    # Same locked M0 non-positive adapter; MERGEABLE requires an explicit MATCH from eligibility/routing caller.
    decisions = {key: 'BLOCKED' if value in ('NOT_MERGEABLE', 'BLOCKED') else
                 'REVIEW' if value in ('MERGEABLE', 'REVIEW', 'DEFERRED') else value
                 for key, value in decisions.items()}
    candidates = set(candidates)
    if len(set(pass_order)) != len(pass_order) or set(pass_order) != set(pass_ids):
        raise ValueError('EVALUATION_PASS_ORDER_MISMATCH')
    positives = {key for key, label in labels.items() if label is PairLabel.POSITIVE}
    seen, passes = set(), {}
    for name in pass_order:
        current = set(pass_ids[name])
        if not current <= candidates:
            raise ValueError('EVALUATION_PASS_OUTSIDE_UNION')
        new = current - seen
        seen.update(current)
        passes[name] = {'unique_pairs': len(current), 'incremental_unique_pairs': len(new),
                        'incremental_positive_pairs': len(new & positives),
                        'cumulative_positive_pairs': len(seen & positives),
                        'cumulative_candidate_recall': len(seen & positives) / len(positives) if positives else None}
    legal = record_count * (record_count - 1) // 2
    if len(candidates) > legal:
        raise ValueError('CANDIDATES_EXCEED_LEGAL_UNIVERSE')
    return {'classification': evaluate_pairs(labels, decisions, candidates),
            'candidate_recall': len(candidates & positives) / len(positives) if positives else None,
            'candidate_recall_reason': None if positives else 'NO_KNOWN_POSITIVE_LABELS',
            'reduction_ratio': 1 - len(candidates) / legal if legal else None,
            'reduction_ratio_reason': None if legal else 'EMPTY_LEGAL_UNIVERSE',
            'passes': passes, 'truth_scope': 'SYNTHETIC_TC1_DIAGNOSTIC_NOT_INDEPENDENT_GOLD'}
