"""Tests for TASK-4.1 (policy YAML loading) and TASK-4.2 (subgroup predicates in YAML)."""

import pathlib
import tempfile

import pytest


YAML_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "src" / "masterone" / "mdm" / "config" / "policy_person_v3.yaml"
)


# ── TASK-4.1: Policy YAML loading ─────────────────────────────────────────────

class TestLoadPolicyYaml:
    def test_yaml_file_exists(self):
        assert YAML_PATH.exists(), f"YAML policy file not found: {YAML_PATH}"

    def test_yaml_loads_all_subgroups(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        policies = load_policy_yaml(YAML_PATH)
        expected = {"A1", "A2", "A3", "C1", "C2", "C3", "C_MULTI",
                    "D1", "D2", "D3", "D4", "D_MULTI"}
        assert expected.issubset(set(policies.keys()))

    def test_yaml_a1_matches_hardcoded(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml, POLICIES
        yaml_pol = load_policy_yaml(YAML_PATH)
        yaml_a1 = yaml_pol["A1"]
        hard_a1 = POLICIES["A1"]
        assert yaml_a1.version == hard_a1.version
        assert yaml_a1.min_match_weight == hard_a1.min_match_weight
        assert yaml_a1.approval_level == hard_a1.approval_level
        assert yaml_a1.required["phone"].states == hard_a1.required["phone"].states
        assert yaml_a1.required["phone"].quality == hard_a1.required["phone"].quality
        assert yaml_a1.forbidden == hard_a1.forbidden

    def test_yaml_a3_required_tags(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        yaml_pol = load_policy_yaml(YAML_PATH)
        a3 = yaml_pol["A3"]
        assert "GIVEN_NAME_COMPATIBLE" in a3.required["name"].required_tags
        assert a3.min_match_weight == 12.0

    def test_yaml_d_subgroups_senior_steward(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        yaml_pol = load_policy_yaml(YAML_PATH)
        for sg in ("D1", "D2", "D3", "D4", "D_MULTI"):
            assert yaml_pol[sg].approval_level == "SENIOR_STEWARD", sg
            assert yaml_pol[sg].min_match_weight == 999.0, sg

    def test_yaml_allow_llm_evidence_always_false(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        yaml_pol = load_policy_yaml(YAML_PATH)
        for sg_id, pol in yaml_pol.items():
            assert pol.allow_llm_evidence is False, f"{sg_id}: allow_llm_evidence should be False"

    def test_missing_yaml_returns_empty(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        result = load_policy_yaml("/nonexistent/path/policy.yaml")
        assert result == {}

    def test_malformed_yaml_returns_empty(self):
        from masterone.mdm.pipeline.policy_engine import load_policy_yaml
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("not_a_dict: [1, 2, 3]\n")
            tmp = f.name
        result = load_policy_yaml(tmp)
        assert result == {}


class TestGetPolicies:
    def test_get_policies_returns_dict(self):
        from masterone.mdm.pipeline.policy_engine import get_policies
        policies = get_policies()
        assert isinstance(policies, dict)
        assert "A1" in policies

    def test_get_policies_yaml_takes_precedence_over_hardcoded(self):
        """When YAML loads successfully, get_policies returns YAML version."""
        from masterone.mdm.pipeline.policy_engine import get_policies, load_policy_yaml
        yaml_pol = load_policy_yaml(YAML_PATH)
        runtime_pol = get_policies()
        # Both should agree on A1 version
        assert runtime_pol["A1"].version == yaml_pol["A1"].version

    def test_apply_policy_result_has_policy_file_version(self):
        from masterone.mdm.pipeline.policy_engine import apply_policy, POLICY_FILE_VERSION
        result = apply_policy("A", {}, 5.0, {}, {})
        assert hasattr(result, "policy_file_version")
        assert result.policy_file_version == POLICY_FILE_VERSION

    def test_policy_file_version_in_score_pair_output(self):
        from masterone.mdm.pipeline.matching import score_pair, POLICY_FILE_VERSION
        from masterone.mdm.pipeline.policy_engine import POLICY_FILE_VERSION as ENG_VER
        left = {
            "id": "l1", "entity_type": "PERSON",
            "raw_fields": {"name": "N", "phone": "0901234567", "email": "", "address": ""},
            "normalized_fields": {
                "name": "nguyen van an", "name_unicode": "nguyen van an",
                "phone": "0901234567",
                "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
                "email": "", "emails": [], "address": "", "address_parts": {},
                "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                   "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
            },
            "quality_flags": [],
        }
        right = {
            "id": "r1", "entity_type": "PERSON",
            "raw_fields": {"name": "N", "phone": "0901234567", "email": "", "address": ""},
            "normalized_fields": {
                "name": "nguyen van an", "name_unicode": "nguyen van an",
                "phone": "0901234567",
                "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
                "email": "", "emails": [], "address": "", "address_parts": {},
                "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                   "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
            },
            "quality_flags": [],
        }
        result = score_pair(left, right, {"exact_phone"})
        assert "policy_file_version" in result
        assert result["policy_file_version"] == ENG_VER


# ── TASK-4.2: Predicates section in YAML ──────────────────────────────────────

class TestYamlPredicates:
    def test_yaml_has_predicates_section(self):
        import yaml
        with YAML_PATH.open() as f:
            raw = yaml.safe_load(f)
        assert "predicates" in raw, "YAML must have a 'predicates' section (TASK-4.2)"

    def test_predicates_cover_all_groups(self):
        import yaml
        with YAML_PATH.open() as f:
            raw = yaml.safe_load(f)
        predicates = raw["predicates"]
        groups = {v["group"] for v in predicates.values() if "group" in v}
        assert "A" in groups
        assert "C" in groups
        assert "D" in groups

    def test_a1_predicate_exists(self):
        import yaml
        with YAML_PATH.open() as f:
            raw = yaml.safe_load(f)
        preds = raw["predicates"]
        a_preds = {k: v for k, v in preds.items() if v.get("group") == "A"}
        # At minimum A1 and A2 predicates should be declared
        assert any("A1" in k for k in a_preds), "No A1 predicate found"
        assert any("A2" in k for k in a_preds), "No A2 predicate found"

    def test_d_predicates_declare_cf_fields(self):
        import yaml
        with YAML_PATH.open() as f:
            raw = yaml.safe_load(f)
        preds = raw["predicates"]
        d_preds = {k: v for k, v in preds.items() if v.get("group") == "D"}
        # At least one D predicate should reference cf_field or cf_fields
        has_cf = any("cf_field" in v or "cf_fields" in v or "min_cf_fields" in v
                     for v in d_preds.values())
        assert has_cf, "D predicates must reference conflict field(s)"
