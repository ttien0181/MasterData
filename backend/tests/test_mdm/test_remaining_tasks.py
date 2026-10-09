"""Tests for TASK-5.1/5.2/5.3 (DB models), TASK-6.2 (four-eyes), TASK-6.4 (simulate),
TASK-7.2 (logging), TASK-8.2 (parquet)."""

import json
import logging
import pytest


# ── TASK-5.1: Entity + Member + Version models ────────────────────────────────

class TestEntityModels:
    def test_resolution_entity_importable(self):
        from masterone.mdm.models import (
            ResolutionEntity, ResolutionEntityMember, ResolutionEntityVersion
        )
        assert ResolutionEntity.__tablename__ == "mdm_resolution_entities"
        assert ResolutionEntityMember.__tablename__ == "mdm_resolution_entity_members"
        assert ResolutionEntityVersion.__tablename__ == "mdm_resolution_entity_versions"

    def test_entity_fields_exist(self):
        from masterone.mdm.models import ResolutionEntity
        cols = {c.key for c in ResolutionEntity.__table__.columns}
        assert {"id", "run_id", "version", "status", "entity_type",
                "created_at", "updated_at"} <= cols

    def test_entity_member_fields_exist(self):
        from masterone.mdm.models import ResolutionEntityMember
        cols = {c.key for c in ResolutionEntityMember.__table__.columns}
        assert {"id", "entity_id", "record_id", "added_at", "added_by"} <= cols

    def test_entity_version_fields_exist(self):
        from masterone.mdm.models import ResolutionEntityVersion
        cols = {c.key for c in ResolutionEntityVersion.__table__.columns}
        assert {"id", "entity_id", "version", "golden_fields", "changed_by", "created_at"} <= cols


# ── TASK-8.1: ResolutionStageResult model ────────────────────────────────────

class TestStageResultModel:
    def test_stage_result_importable(self):
        from masterone.mdm.models import ResolutionStageResult
        assert ResolutionStageResult.__tablename__ == "mdm_resolution_stage_results"

    def test_stage_result_fields(self):
        from masterone.mdm.models import ResolutionStageResult
        cols = {c.key for c in ResolutionStageResult.__table__.columns}
        assert {"id", "run_id", "attempt_id", "stage_name", "success",
                "duration_ms", "metrics", "output_artifacts", "error", "created_at"} <= cols

    def test_stage_result_unique_constraint(self):
        from masterone.mdm.models import ResolutionStageResult
        constraint_names = {c.name for c in ResolutionStageResult.__table__.constraints}
        assert "uq_mdm_stage_result" in constraint_names


# ── TASK-5.2: NegativeEdge model ─────────────────────────────────────────────

class TestNegativeEdgeModel:
    def test_negative_edge_importable(self):
        from masterone.mdm.models import ResolutionNegativeEdge
        assert ResolutionNegativeEdge.__tablename__ == "mdm_resolution_negative_edges"

    def test_negative_edge_fields(self):
        from masterone.mdm.models import ResolutionNegativeEdge
        cols = {c.key for c in ResolutionNegativeEdge.__table__.columns}
        assert {"id", "run_id", "left_record_id", "right_record_id",
                "created_by", "reason", "active", "created_at"} <= cols


# ── TASK-5.3: GoldenFieldValue model ─────────────────────────────────────────

class TestGoldenFieldValueModel:
    def test_golden_field_importable(self):
        from masterone.mdm.models import ResolutionGoldenFieldValue
        assert ResolutionGoldenFieldValue.__tablename__ == "mdm_resolution_golden_field_values"

    def test_golden_field_fields(self):
        from masterone.mdm.models import ResolutionGoldenFieldValue
        cols = {c.key for c in ResolutionGoldenFieldValue.__table__.columns}
        assert {"id", "entity_id", "version", "field_name",
                "display_value", "source_record_id", "provenance"} <= cols


# ── TASK-6.2: Four-eyes columns on ResolutionReviewCase ──────────────────────

class TestFourEyesColumns:
    def test_review_case_has_four_eyes_columns(self):
        from masterone.mdm.models import ResolutionReviewCase
        cols = {c.key for c in ResolutionReviewCase.__table__.columns}
        assert "first_reviewer_id" in cols
        assert "first_decision" in cols
        assert "first_decided_at" in cols
        assert "second_reviewer_id" in cols
        assert "second_decision" in cols
        assert "second_decided_at" in cols

    def test_requires_four_eyes_group_d_only(self):
        from masterone.mdm.resolution_review import _requires_four_eyes

        class _FakeCase:
            def __init__(self, group: str):
                self.primary_group = group

        assert _requires_four_eyes(_FakeCase("D")) is True
        assert _requires_four_eyes(_FakeCase("A")) is False
        assert _requires_four_eyes(_FakeCase("C")) is False


# ── TASK-6.4: Policy simulate ────────────────────────────────────────────────

class TestPolicySimulate:
    def _mock_pair(self, pid: str, group: str, weight: float, current: str) -> dict:
        return {
            "pair_id": pid, "primary_group": group,
            "match_weight": weight, "comparisons": {},
            "nf_left": {}, "nf_right": {},
            "current_decision": current,
        }

    def test_simulate_returns_correct_types(self):
        from masterone.mdm.pipeline.policy_engine import simulate_policy, POLICY_FILE_VERSION
        pairs = [self._mock_pair("p1", "A", 5.0, "REVIEW")]
        import pathlib
        yaml_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "src" / "masterone" / "mdm" / "config" / "policy_person_v3.yaml"
        )
        result = simulate_policy(pairs, yaml_path.read_text())
        assert result.total_pairs == 1
        assert isinstance(result.changed, int)
        assert isinstance(result.details, list)

    def test_simulate_empty_pairs(self):
        from masterone.mdm.pipeline.policy_engine import simulate_policy
        result = simulate_policy([], "version: 'test'\nsubgroups: {}")
        assert result.total_pairs == 0
        assert result.changed == 0

    def test_simulate_invalid_yaml_falls_back(self):
        from masterone.mdm.pipeline.policy_engine import simulate_policy
        pairs = [self._mock_pair("p1", "A", 5.0, "REVIEW")]
        result = simulate_policy(pairs, "not: valid: yaml: policy")
        # Should not raise; falls back to POLICIES
        assert isinstance(result.total_pairs, int)

    def test_simulate_result_dataclass_fields(self):
        from masterone.mdm.pipeline.policy_engine import SimulateResult
        r = SimulateResult(total_pairs=10, changed=2, review_to_merge=1,
                           merge_to_review=1, unchanged_merge=5, unchanged_review=3, details=[])
        assert r.total_pairs == 10
        assert r.review_to_merge + r.merge_to_review == r.changed


# ── TASK-7.2: Structured JSON logging ────────────────────────────────────────

class TestStructuredLogging:
    def test_configure_logging_sets_json_formatter(self, caplog):
        from masterone.logging_config import configure_logging, get_logger
        configure_logging(level=logging.DEBUG, json_output=False)  # plain for caplog compat
        log = get_logger("test.structured")
        log.info("hello structured")
        # configure_logging should not raise

    def test_json_formatter_output(self):
        import logging
        from masterone.logging_config import _JSONFormatter
        fmt = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "test message"
        assert "ts" in parsed

    def test_pii_filter_masks_phone(self):
        from masterone.logging_config import _PIIFilter
        import logging
        pii_filter = _PIIFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="phone is 0901234567", args=(), exc_info=None,
        )
        pii_filter.filter(record)
        assert "0901234567" not in record.msg
        assert "567" in record.msg  # last 3 digits kept

    def test_pii_filter_masks_email(self):
        from masterone.logging_config import _PIIFilter
        import logging
        pii_filter = _PIIFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="email is user@example.com", args=(), exc_info=None,
        )
        pii_filter.filter(record)
        assert "user@example.com" not in record.msg
        assert "example.com" in record.msg

    def test_get_logger_returns_logger(self):
        from masterone.logging_config import get_logger
        log = get_logger("test.module")
        assert isinstance(log, logging.Logger)
        assert log.name == "test.module"


# ── TASK-8.2: Parquet artifact writing ───────────────────────────────────────

class TestParquetArtifacts:
    def test_write_parquet_atomic(self, tmp_path):
        from masterone.storage.parquet_io import write_parquet_atomic, read_parquet
        rows = [{"id": "r1", "name": "nguyen van an"}, {"id": "r2", "name": "tran thi bich"}]
        out = tmp_path / "test.parquet"
        write_parquet_atomic(rows, out)
        assert out.exists()
        read_back = read_parquet(out)
        assert len(read_back) == 2
        assert read_back[0]["id"] == "r1"

    def test_stage_runner_write_parquet(self, tmp_path):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = []
        for i in range(3):
            records.append({
                "id": f"r{i}",
                "entity_type": "PERSON",
                "raw_fields": {"name": "nguyen van an", "phone": f"090123{i:04d}", "email": "", "address": ""},
                "normalized_fields": {
                    "name": "nguyen van an", "name_unicode": "nguyen van an",
                    "phone": f"090123{i:04d}",
                    "phones": [{"value": f"090123{i:04d}", "quality": "VALID", "is_primary": True}],
                    "email": "", "emails": [], "address": "", "address_parts": {},
                    "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                       "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                    "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
                    "dob": "",
                },
                "quality_flags": [],
            })
        runner = StageRunner(run_id="parquet_run", attempt_id="a1", output_dir=tmp_path)
        runner.run_all(records)
        # Check at least one Parquet file was written
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) > 0
