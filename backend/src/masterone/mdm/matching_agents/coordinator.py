"""Join evidence by canonical key; output cannot change matching decisions."""
from copy import deepcopy


def _index(rows):
    indexed = {}
    for row in rows:
        key = row['key']
        if key in indexed:
            raise ValueError('DUPLICATE_AGENT_PAIR')
        indexed[key] = row
    return indexed


def join_evidence(expected_keys, rules, embeddings, *, expected_encoder_digest,
                  expected_preprocessing_digest, fallback_mode='fail_operation', snapshot_coverage=None,
                  copy_evidence=True):
    if not expected_encoder_digest or not expected_preprocessing_digest:
        raise ValueError('UNPINNED_ENCODER_IDENTITY')
    if fallback_mode not in ('fail_operation', 'rule_only'):
        raise ValueError('UNPINNED_FALLBACK_MODE')
    expected = set(expected_keys)
    if len(expected) != len(expected_keys):
        raise ValueError('DUPLICATE_CANDIDATE_PAIR')
    rule_index, embedding_index = _index(rules), _index(embeddings)
    if set(rule_index) != expected or not set(embedding_index) <= expected:
        raise ValueError('AGENT_PAIR_COVERAGE_MISMATCH')
    if any(row['status'] != 'OK' for row in rule_index.values()):
        raise ValueError('RULE_AGENT_FAILED')
    degraded = set(embedding_index) != expected
    if snapshot_coverage is not None:
        if set(snapshot_coverage) != {'name', 'address'}:
            raise ValueError('EMBEDDING_SNAPSHOT_COVERAGE_MISSING')
        for field in snapshot_coverage.values():
            if not 0 <= field['error_records'] <= field['eligible_records']:
                raise ValueError('EMBEDDING_SNAPSHOT_COVERAGE_INVALID')
            degraded |= field['error_records'] > 0
    for row in embedding_index.values():
        if (row.get('encoder_digest') != expected_encoder_digest
                or row.get('preprocessing_digest') != expected_preprocessing_digest):
            raise ValueError('EMBEDDING_IDENTITY_MISMATCH')
        fields = row.get('fields', {})
        if set(fields) != {'name', 'address'}:
            raise ValueError('EMBEDDING_FIELD_CONTRACT_MISMATCH')
        degraded |= any(value.status == 'ERROR' for value in fields.values())
    if degraded and fallback_mode == 'fail_operation':
        raise ValueError('EMBEDDING_COVERAGE_FAILED')
    # The worker owns intermediate rows until sealing; avoid duplicating a full
    # generation in RAM. Other callers retain independent copies by default.
    copy_row = deepcopy if copy_evidence else lambda row: row
    return {'status': 'DEGRADED' if degraded else 'OK', 'automation_allowed': False,
            'field_coverage': snapshot_coverage or {},
            'coverage': {'expected_pairs': len(expected), 'rule_pairs': len(rule_index),
                         'embedding_pairs': len(embedding_index)},
            'pairs': [{'key': key, 'rule': copy_row(rule_index[key]),
                       'embedding': copy_row(embedding_index.get(key))} for key in sorted(expected)]}
