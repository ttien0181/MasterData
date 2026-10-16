import importlib
from pathlib import Path

import pytest


def activation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'scripts'))
    return importlib.import_module('activate_api_matching_defaults')


def proof(digests):
    return {'status': 'PASS', 'runs': [
        {'variant': 'evidence', 'status': 'PASS', 'profile_digest': digest,
         'machine_quality_before_truth_replay': {'candidate_recall': 1.},
         'fixture': {'record_count': 80},
         'final_quality_after_scripted_truth_review': {'records_preserved': 80}}
        for digest in digests]}


def test_activation_rejects_a_stale_runtime_before_writing(monkeypatch):
    module = activation(monkeypatch)
    with pytest.raises(ValueError, match='MEASURED_PROFILE_RUNTIME_MISMATCH'):
        module.validate_activation_proof(proof(['a'*64, 'a'*64]), 'b'*64)


def test_activation_requires_one_consistent_measured_configuration(monkeypatch):
    module = activation(monkeypatch)
    with pytest.raises(ValueError, match='INCONSISTENT_MEASURED_PROFILES'):
        module.validate_activation_proof(proof(['a'*64, 'b'*64]), 'a'*64)
    assert module.validate_activation_proof(proof(['a'*64, 'a'*64]), 'a'*64) == 'a'*64
