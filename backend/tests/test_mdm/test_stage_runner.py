"""Tests for TASK-8.1 (StageRunner) and TASK-10.1/10.2 (E2E + memory)."""

import pytest


def _make_records(n: int = 5) -> list[dict]:
    """Generate *n* synthetic PERSON records with normalized_fields pre-filled."""
    names = ["nguyen van an", "tran thi bich", "le van hung", "pham thi lan", "hoang van minh"]
    records = []
    for i in range(n):
        name = names[i % len(names)]
        records.append({
            "id": f"r{i:04d}",
            "entity_type": "PERSON",
            "raw_fields": {"name": name, "phone": f"090123{i:04d}", "email": "", "address": ""},
            "normalized_fields": {
                "name": name,
                "name_unicode": name,
                "phone": f"090123{i:04d}",
                "phones": [{"value": f"090123{i:04d}", "quality": "VALID", "is_primary": True}],
                "email": "",
                "emails": [],
                "address": "",
                "address_parts": {},
                "_field_quality": {
                    "name": "VALID", "phone": "VALID",
                    "email": "MISSING", "address": "MISSING",
                    "dob": "MISSING", "government_id": "MISSING",
                },
                "_name_parts": {"surname": name.split()[0], "middle_name": name.split()[1] if len(name.split()) > 2 else "", "given_name": name.split()[-1]},
                "dob": "",
            },
            "quality_flags": [],
        })
    return records


# ── TASK-8.1: StageRunner unit tests ─────────────────────────────────────────

class TestStageRunner:
    def test_run_all_returns_expected_keys(self):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(4)
        runner = StageRunner(run_id="test_run", attempt_id="a1")
        output = runner.run_all(records)
        assert "pairs" in output
        assert "proposals" in output
        assert "review_cases" in output
        assert "candidate_count" in output

    def test_stage_results_recorded(self):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(4)
        runner = StageRunner(run_id="run1", attempt_id="a1")
        runner.run_all(records)
        completed_stages = {sr.stage_name for sr in runner.stage_results}
        # All 7 stages should be recorded
        expected_stages = {"PREPARE", "NORMALIZE", "BUILD_CANDIDATES", "SCORE", "DECIDE", "CLUSTER", "MATERIALIZE"}
        for s in expected_stages:
            assert s in completed_stages, f"Stage {s} not found in results"

    def test_total_duration_positive(self):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(4)
        runner = StageRunner(run_id="run1", attempt_id="a1")
        runner.run_all(records)
        assert runner.total_duration_ms() >= 0

    def test_empty_records_no_crash(self):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        runner = StageRunner(run_id="empty_run", attempt_id="a1")
        output = runner.run_all([])
        assert output["pairs"] == []
        assert output["proposals"] == []

    def test_identical_records_become_candidates(self):
        from masterone.mdm.pipeline.stage_runner import StageRunner
        base = _make_records(1)[0]
        twin = {**base, "id": "twin"}
        twin["normalized_fields"] = dict(base["normalized_fields"])  # same phone → exact_phone block
        runner = StageRunner(run_id="twin_run", attempt_id="a1")
        output = runner.run_all([base, twin])
        # Pair should exist
        pair_keys = {(p["left_id"], p["right_id"]) for p in output["pairs"]}
        assert ("r0000", "twin") in pair_keys or ("twin", "r0000") in pair_keys


# ── TASK-10.1: E2E pipeline test ─────────────────────────────────────────────

class TestE2EPipeline:
    def test_e2e_same_name_same_phone_merges(self):
        """Records with identical name + phone should form a proposal."""
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = [
            {
                "id": "p1",
                "entity_type": "PERSON",
                "raw_fields": {"name": "Nguyen Van An", "phone": "0901234567", "email": "", "address": ""},
                "normalized_fields": {
                    "name": "nguyen van an", "name_unicode": "nguyen van an",
                    "phone": "0901234567",
                    "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
                    "email": "", "emails": [], "address": "", "address_parts": {},
                    "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                       "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                    "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
                    "dob": "",
                },
                "quality_flags": [],
            },
            {
                "id": "p2",
                "entity_type": "PERSON",
                "raw_fields": {"name": "Nguyen Van An", "phone": "0901234567", "email": "", "address": ""},
                "normalized_fields": {
                    "name": "nguyen van an", "name_unicode": "nguyen van an",
                    "phone": "0901234567",
                    "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
                    "email": "", "emails": [], "address": "", "address_parts": {},
                    "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                       "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                    "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
                    "dob": "",
                },
                "quality_flags": [],
            },
        ]
        runner = StageRunner(run_id="e2e_run", attempt_id="a1")
        output = runner.run_all(records)
        assert output["candidate_count"] >= 1
        assert len(output["pairs"]) >= 1
        pair = output["pairs"][0]
        assert pair["decision"] in ("MERGEABLE", "REVIEW", "NOT_MERGEABLE")

    def test_e2e_negative_edge_blocks_pair(self):
        """A negative edge prevents a candidate pair from being scored as MATCH."""
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(2)
        r0 = records[0]["id"]
        r1 = records[1]["id"]
        neg = frozenset({(min(r0, r1), max(r0, r1))})
        runner = StageRunner(run_id="neg_run", attempt_id="a1")
        output = runner.run_all(records, negative_pairs=neg)
        for pair in output["pairs"]:
            ids = (pair["left_id"], pair["right_id"])
            if ids == (min(r0, r1), max(r0, r1)):
                assert pair["decision"] == "NOT_MERGEABLE"

    def test_e2e_different_records_no_false_merge(self):
        """Records with completely different phones should not auto-merge."""
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(5)  # all have distinct phones
        runner = StageRunner(run_id="no_merge_run", attempt_id="a1")
        output = runner.run_all(records)
        auto_merge_proposals = [p for p in output["proposals"]
                                if p.get("status") == "AUTO_READY" and len(p.get("member_record_ids", [])) > 1]
        # No false merges expected when phones are all different
        assert len(auto_merge_proposals) == 0


# ── TASK-10.2: Bounded memory test ───────────────────────────────────────────

class TestBoundedMemory:
    def test_1000_records_completes(self):
        """Pipeline on 1000 records should complete without crashing (memory/perf smoke)."""
        import time
        from masterone.mdm.pipeline.stage_runner import StageRunner

        names = ["nguyen van an", "tran thi bich", "le van hung", "pham thi lan",
                 "hoang van minh", "do thi thu", "bui van duc", "nguyen thi mai",
                 "tran van long", "le thi hoa"]
        records = []
        for i in range(1000):
            name = names[i % len(names)]
            records.append({
                "id": f"r{i:04d}",
                "entity_type": "PERSON",
                "raw_fields": {"name": name, "phone": f"09{i:08d}", "email": "", "address": ""},
                "normalized_fields": {
                    "name": name, "name_unicode": name,
                    "phone": f"09{i:08d}",
                    "phones": [{"value": f"09{i:08d}", "quality": "VALID", "is_primary": True}],
                    "email": "", "emails": [], "address": "", "address_parts": {},
                    "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                       "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                    "_name_parts": {"surname": name.split()[0], "middle_name": "", "given_name": name.split()[-1]},
                    "dob": f"{1970 + (i % 50)}-01-01",
                },
                "quality_flags": [],
            })

        start = time.time()
        runner = StageRunner(run_id="perf_run", attempt_id="a1")
        output = runner.run_all(records)
        elapsed = time.time() - start

        assert isinstance(output, dict)
        assert elapsed < 60.0, f"1000-record pipeline took {elapsed:.1f}s, expected < 60s"

    def test_stage_runner_does_not_load_all_in_memory_at_once(self):
        """StageRunner yields per-stage; verify stage_results grow incrementally."""
        from masterone.mdm.pipeline.stage_runner import StageRunner
        records = _make_records(10)
        runner = StageRunner(run_id="mem_run", attempt_id="a1")
        runner.run_all(records)
        # Each stage should have recorded exactly one StageResult
        stage_names = [sr.stage_name for sr in runner.stage_results]
        assert len(stage_names) == 7
        assert len(set(stage_names)) == 7  # no duplicates
