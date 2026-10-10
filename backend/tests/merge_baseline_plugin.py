"""Opt-in scratch isolation for the merge baseline runner only.

Production StageRunner is unchanged. Explicit output directories in tests are
respected; its legacy shared default is redirected to this test's tmp_path.
"""

from functools import wraps

import pytest


@pytest.fixture(autouse=True)
def isolated_stage_runner_scratch(tmp_path, monkeypatch):
    from masterone.mdm.pipeline.stage_runner import StageRunner

    original = StageRunner.__init__

    @wraps(original)
    def initialize(self, run_id, attempt_id, output_dir=None, db_url=None):
        return original(self, run_id, attempt_id,
                        output_dir=tmp_path / "stages" if output_dir is None else output_dir,
                        db_url=db_url)

    monkeypatch.setattr(StageRunner, "__init__", initialize)
