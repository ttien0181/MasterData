"""Adapt already-produced SCORE/DECIDE evidence; never rescore or change policy."""
import json
from copy import deepcopy

from .contracts import PairKey


def adapt_rule(engine, scored, baseline, *, owner_id, run_id, workflow_pair_id=None):
    key = PairKey(owner_id, run_id, scored.left_id, scored.right_id)
    if engine.config.mode != 'rule' or engine.identity['model_version'] != 'p119_rule_v1':
        raise ValueError('RULE_AGENT_REQUIRES_RULE_SCORE')
    if scored.model_hash != engine.identity['model_hash'] or scored.config_hash != engine.identity['config_hash']:
        raise ValueError('RULE_IDENTITY_MISMATCH')
    if (baseline.get('left_id'), baseline.get('right_id')) != (key.left_id, key.right_id):
        raise ValueError('RULE_BASELINE_PAIR_MISMATCH')
    evidence = json.loads(scored.evidence_json)
    records = engine.snapshot.get_records({key.left_id, key.right_id})
    if len(records) != 2:
        raise ValueError('RULE_SNAPSHOT_ENDPOINT_MISSING')
    frequencies = {}
    for rid, row in records.items():
        normalized = row['normalized_fields']
        frequencies[rid] = {}
        for field in ('phone', 'email'):
            values = {entry['value'] for entry in normalized.get(field + 's', [])
                      if entry.get('quality') == 'VALID' and entry.get('value')}
            if not values and normalized.get('_field_quality', {}).get(field) == 'VALID':
                values = {normalized[field]} if normalized.get(field) else set()
            frequencies[rid][field] = {value: engine.frequencies[field][value] for value in sorted(values)}
    return {'key': key, 'workflow_pair_id': workflow_pair_id, 'status': 'OK',
            'rule_score': scored.rule_score, 'score_unit': 'p119_rule_v1',
            'original_baseline': deepcopy(baseline), 'field_evidence': evidence.get('field_evidence'),
            'normalization_flags': evidence.get('normalization_tags'),
            'quality': {rid: deepcopy(row['normalized_fields'].get('_field_quality', {}))
                        for rid, row in records.items()},
            'cannot_link': (key.left_id, key.right_id) in engine.negative_pairs,
            'frequency_evidence': frequencies,
            'constraint_evidence': deepcopy({k: v for k, v in baseline.items()
                                            if 'constraint' in k or 'conflict' in k}),
            'scoring_evidence': evidence, 'identity': deepcopy(engine.identity),
            'normalization_hash': next((value for path, value in engine.rule_sources.items()
                                       if path.name == 'standardization.py'), None),
            'trace_id': key.digest}
