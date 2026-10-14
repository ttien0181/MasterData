"""Original bundle manifests are authority; cache paths never escape ownership."""
import importlib.util
from pathlib import Path

import pytest


def helper():
    path = Path(__file__).resolve().parents[3] / 'scripts/fetch_cluster_bundles.py'
    spec = importlib.util.spec_from_file_location('cluster_fetch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('relative', ['../escape.csv', 'C:/private.csv', '/absolute.csv', 'raw/../../escape.csv', 'raw\\file.csv'])
def test_manifest_paths_are_relative_and_inside_owned_cache(tmp_path, relative):
    with pytest.raises(ValueError):
        helper().target_path(tmp_path, relative)


def test_changed_existing_source_is_not_overwritten(tmp_path):
    import hashlib
    row = tmp_path / 'source.csv'
    row.write_bytes(b'changed')
    with pytest.raises(ValueError, match='INPUT_HASH_MISMATCH'):
        helper().verify_existing(row, hashlib.sha256(b'original').hexdigest())
    assert row.read_bytes() == b'changed'
