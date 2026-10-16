"""Runtime portability must retain model/lock integrity and account local inference."""
import json
from pathlib import Path

import pytest
from masterone.config import get_settings
from masterone.mdm.matching_agents import encoder_runtime as runtime

TRIAL = Path(__file__).resolve().parents[3] / 'artifacts/advanced-m2a-20261014/e5-trial-01'


def linux_root(tmp_path, monkeypatch):
    (tmp_path / 'model-manifest.json').write_bytes((TRIAL / 'model-manifest.json').read_bytes())
    (tmp_path / 'requirements-lock.txt').write_text('torch==2.14.1+cpu\n', encoding='utf8')
    monkeypatch.setattr(get_settings(), 'embedding_runtime_root', str(tmp_path))
    monkeypatch.setattr(runtime.sys, 'platform', 'linux')
    monkeypatch.setattr(runtime, 'LINUX_LOCK', runtime.file_hash(tmp_path / 'requirements-lock.txt'))
    return tmp_path


def test_linux_runtime_uses_pinned_cpu_model_and_posix_interpreter(tmp_path, monkeypatch):
    root = linux_root(tmp_path, monkeypatch)
    resolved, identity = runtime.runtime_spec()
    assert resolved == root
    assert identity['platform'] == 'linux'
    assert identity['python'] == '3.11'
    assert identity['model_id'] == 'intfloat/multilingual-e5-small'
    assert identity['bundle_digest'] == runtime.BUNDLE
    assert runtime.runtime_python(root) == root / 'env/bin/python'


def test_linux_rejects_unapproved_dependencies(tmp_path, monkeypatch):
    root = linux_root(tmp_path, monkeypatch)
    (root / 'requirements-lock.txt').write_text('torch==0.0.0\n', encoding='utf8')
    with pytest.raises(ValueError, match='APPROVED_IDENTITY'):
        runtime.runtime_spec()


def test_linux_rejects_changed_model_manifest(tmp_path, monkeypatch):
    root = linux_root(tmp_path, monkeypatch)
    manifest = json.loads((root / 'model-manifest.json').read_text())
    manifest['files'][0]['sha256'] = '0' * 64
    (root / 'model-manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError, match='MANIFEST_CORRUPT'):
        runtime.runtime_spec()


def test_encoder_accounts_tokens_and_requests_from_worker_reply():
    encoder = runtime.EncoderRuntime({}, runtime.BUDGET)
    encoder.record_usage('name', ['example'], {'input_tokens': 4, 'vectors': [[1.0]]})
    encoder.record_usage('address', ['other', 'third'], {'input_tokens': 9, 'vectors': [[1.0], [1.0]]})
    assert encoder.usage == {
        'name': {'batches': 1, 'texts': 1, 'input_tokens': 4},
        'address': {'batches': 1, 'texts': 2, 'input_tokens': 9},
    }
