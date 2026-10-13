"""Parallel rule-mode SCORE must produce the same pairs as in-process SCORE."""

import pytest

from masterone.mdm.pipeline.stage_runner import StageRunner


def _record(i):
    name = f"nguyen van {i // 4}"
    phone = f"09{(i // 2):08d}"
    return {
        "id": f"r{i:04d}", "entity_type": "PERSON", "source_system": "CRM",
        "raw_fields": {"name": name, "phone": phone, "email": "", "address": ""},
        "normalized_fields": {
            "name": name, "name_unicode": name, "phone": phone,
            "phones": [{"value": phone, "quality": "VALID", "is_primary": True}],
            "email": "", "emails": [], "address": "", "address_parts": {},
            "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                               "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
            "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": f"{i // 4}"},
            "dob": f"{1970 + (i % 20)}-01-01",
        },
        "quality_flags": [],
    }


@pytest.mark.parametrize("workers", ["2"])
def test_parallel_score_matches_serial(monkeypatch, workers):
    monkeypatch.setenv("SOURCE_MAPPING_VERSION", "test")
    monkeypatch.setenv("MDM_SCORE_WORKERS", workers)
    records = [_record(i) for i in range(400)]

    monkeypatch.setenv("MDM_SCORE_PARALLEL_MIN", "10000000")
    serial_runner = StageRunner(run_id="par_test", attempt_id="a")
    serial = serial_runner.run_all(records)
    # The next run rebuilds the same index; release its Windows file handle.
    serial_runner.close_snapshot()
    monkeypatch.setenv("MDM_SCORE_PARALLEL_MIN", "0")
    parallel_runner = StageRunner(run_id="par_test", attempt_id="a")
    parallel = parallel_runner.run_all(records)

    def normalized(output):
        return sorted(
            (p["left_id"], p["right_id"], p["decision"], p["reason"], p["match_weight"], tuple(p["blocking_reasons"]))
            for p in output["pairs"]
        )

    assert normalized(parallel) == normalized(serial)
    assert len(serial["pairs"]) > 0
    parallel_runner.close_snapshot()
    for runner in (serial_runner, parallel_runner):
        runner.close_decisions()
        runner.con.close()
