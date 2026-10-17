"""Frozen JSON export must not hydrate the whole generation in Python."""
import sqlite3

import pytest
from fastapi import HTTPException
from masterone.mdm.generation_staging import digest
from masterone.mdm.workflow_models import WorkflowGeneration
from sqlalchemy import inspect


def test_spool_preserves_frozen_values_and_verifies_hash(client, workflow_run):
    from masterone.mdm.workflow_export_spool import spool_result
    result = {'golden_revision': 4, 'records': {'z': {'source_system': 'CRM'},
        'a': {'source_system': 'BILL', 'raw': 'Tiếng Việt\n"'}},
        'clusters': [{'id': 'c', 'member_record_ids': ['z', 'a'], 'golden_fields': {'fields': {}}}],
        'master_id_version': 'workflow_cluster_v1'}
    # A generation needs an artifact FK only on databases enforcing foreign keys;
    # the API fixture's SQLite mirrors the existing unit-test contract.
    with client.session_factory.begin() as db:
        db.add(WorkflowGeneration(id='frozen', run_id=workflow_run, revision=1,
            artifact_id='fixture', finalized_result=result, result_hash=digest(result)))
    with client.session_factory() as db, sqlite3.connect(':memory:') as state:
        view = spool_result(db, 'frozen', digest(result), 4, state)
        assert list(view.iter_groups()) == result['clusters']
        assert list(view.record_ids()) == ['a', 'z']
        assert view.record('a') == result['records']['a']
        assert view.coverage['total_records'] == 2
        assert all('finalized_result' in inspect(row).unloaded for row in db.identity_map.values()
                   if isinstance(row, WorkflowGeneration))
    with client.session_factory() as db, sqlite3.connect(':memory:') as state:
        with pytest.raises(HTTPException):
            spool_result(db, 'frozen', 'bad-hash', 4, state)
    with client.session_factory() as db, sqlite3.connect(':memory:') as state:
        with pytest.raises(HTTPException):
            spool_result(db, 'frozen', digest(result), 5, state)


@pytest.mark.parametrize('fault', ['corrupt-upload', 'revision-after-upload'])
def test_export_never_commits_corrupt_or_stale_snapshot(client, workflow_run, monkeypatch, tmp_path, fault):
    # This module binds get_store at import time. Load the production binding
    # before injecting the export's late-imported store, so later tests isolate IO.
    import masterone.mdm.workflow_snapshot_storage  # noqa: F401
    from masterone.auth.models import User
    from masterone.config import get_settings
    from masterone.mdm.models import ResolutionExport, ResolutionRun
    from masterone.mdm.workflow_exports import create_export
    from masterone.storage.object_store import LocalObjectStore

    from tests.test_mdm.test_workflow_contract import request
    from tests.test_mdm.test_workflow_golden import golden_request, ready_golden
    from tests.test_mdm.test_workflow_stages import read
    clusters = ready_golden(client, workflow_run)
    root = f'/api/v1/mdm/runs/{workflow_run}/workflow'
    for cluster in clusters:
        response = client.post(root + f"/clusters/{cluster['cluster_id']}/golden/confirm",
                               json=golden_request(client, workflow_run))
        assert response.status_code == 200
    current = read(client, workflow_run)
    assert client.post(root + '/finalize-golden', json=request(current['workflow_revision'],
        expected_golden_revision=current['golden_revision'])).status_code == 200
    class Store(LocalObjectStore):
        def upload_file(self, *args):
            super().upload_file(*args)
            if fault == 'revision-after-upload':
                with client.session_factory.begin() as other:
                    other.get(ResolutionRun, workflow_run).golden_revision += 1
        def iter_chunks(self, *args):
            if fault == 'corrupt-upload':
                yield b'corrupt'
            else:
                yield from super().iter_chunks(*args)
    monkeypatch.setattr(get_settings(), 'export_artifact_dir', str(tmp_path))
    monkeypatch.setattr('masterone.storage.r2.get_store', lambda _: Store(tmp_path / 'remote'))
    with client.session_factory() as db:
        actor = db.query(User).one().id
        with pytest.raises(HTTPException):
            create_export(db, workflow_run, actor, False)
        db.rollback()
        assert db.query(ResolutionExport).filter_by(run_id=workflow_run).count() == 0
