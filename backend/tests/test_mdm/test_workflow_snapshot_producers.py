"""Source fences and cursor producers agree with the historic small snapshot."""
import pytest
from masterone.mdm.models import ResolutionRecord, ResolutionRun

from tests.test_mdm.test_workflow_stages import ready_decisions


@pytest.mark.parametrize('assessed', [False, True])
def test_db_stream_snapshot_matches_current_assessment_without_loading_artifact(client, workflow_run, monkeypatch, assessed):
    from masterone.config import get_settings
    from masterone.mdm.workflow_finalize import decision_snapshot
    from masterone.mdm.workflow_snapshot_producers import snapshot_streams
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', assessed)
    ready_decisions(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        expected = decision_snapshot(db, run, allow_partial=True)
        from sqlalchemy.orm import Query
        monkeypatch.setattr(Query, 'all', lambda *_: (_ for _ in ()).throw(AssertionError('Full query hydration')))
        metadata, streams = snapshot_streams(db, run, allow_partial=True)
        assert metadata['input_hash'] == expected['input_hash']
        assert metadata['execution_identity'] == expected['execution_identity']
        assert list(streams['records']) == expected['records']
        assert list(streams['pairs']) == expected['pairs']
        assert {r['id']: r['value'] for r in streams['overlays']} == expected['overlays']
        assert [(r['left_id'], r['right_id']) for r in streams['negative_pairs']] == expected['negative_pairs']


def test_streamed_snapshot_validation_rejects_changed_raw_input(client, workflow_run):
    from masterone.mdm.workflow_snapshot_producers import snapshot_streams, validate_streamed_snapshot
    ready_decisions(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        metadata, _ = snapshot_streams(db, run)
        validate_streamed_snapshot(db, run, metadata)
    with client.session_factory.begin() as db:
        row = db.query(ResolutionRecord).filter_by(run_id=workflow_run).first()
        row.raw_fields = {**row.raw_fields, 'name': 'Changed source after seal'}
    with client.session_factory() as db, pytest.raises(ValueError, match='ASSESSMENT_INPUT_CHANGED|WORKFLOW_INPUT_CHANGED'):
        validate_streamed_snapshot(db, db.get(ResolutionRun, workflow_run), metadata)


@pytest.mark.parametrize('assessed', [False, True])
def test_current_snapshot_fences_do_not_collect_all_records(client, workflow_run, monkeypatch, assessed):
    from masterone.config import get_settings
    from masterone.mdm import workflow_assessment, workflow_finalize
    monkeypatch.setattr(get_settings(), 'pair_assessment_auto_enabled', assessed)
    ready_decisions(client, workflow_run)
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        snapshot = workflow_finalize.decision_snapshot(db, run)
        old_digest = workflow_assessment.digest
        def bounded(value):
            if isinstance(value, list) and value and isinstance(value[0], dict) and 'raw_fields' in value[0]:
                raise AssertionError('Full record list hashing')
            return old_digest(value)
        monkeypatch.setattr(workflow_assessment, 'digest', bounded)
        monkeypatch.setattr(workflow_finalize, 'digest', bounded)
        workflow_finalize.validate_snapshot_current(db, run, snapshot)
