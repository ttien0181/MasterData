from masterone.config import get_settings
from masterone.mdm.heavy_jobs import heavy_job_slot


def test_import_and_worker_cannot_share_slot_and_lock_releases(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), 'heavy_jobs_serialized', True)
    monkeypatch.setattr(get_settings(), 'heavy_job_lock_path', str(tmp_path / 'heavy.lock'))
    with heavy_job_slot(blocking=False) as first:
        assert first
        with heavy_job_slot(blocking=False) as second:
            assert not second
    with heavy_job_slot(blocking=False) as third:
        assert third


def test_worker_reports_fixed_embedding_limits_without_exposing_exception_details():
    from masterone.mdm.resolution_worker import _safe_failure_code
    assert _safe_failure_code(ValueError('EMBEDDING_RUN_BUDGET_EXCEEDED')) == 'EMBEDDING_RUN_BUDGET_EXCEEDED'
    assert _safe_failure_code(ValueError('EMBEDDING_HTTP_429')) == 'EMBEDDING_HTTP_429'
    assert _safe_failure_code(RuntimeError('https://user:password@example.test')) == 'PIPELINE_FAILED'
    assert _safe_failure_code(ValueError('EMBEDDING_ERROR secret-value')) == 'PIPELINE_FAILED'
