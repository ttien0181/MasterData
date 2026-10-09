"""Tests for IMPROVEMENT_PLAN.md Phase 0–7 tasks."""

import pytest


# ── TASK-0.1: LLM Settings mapping ───────────────────────────────────────────

def test_settings_has_all_llm_fields():
    from masterone.config import Settings
    s = Settings(llm_enabled=True, openrouter_api_key="test-key")
    assert s.llm_enabled is True
    assert s.openrouter_api_key == "test-key"
    assert s.llm_semantic_model == "google/gemini-2.5-flash"
    assert s.llm_max_concurrency == 5
    assert s.llm_cache_ttl_seconds == 86400
    assert s.llm_allow_auto_merge_evidence is False


def test_llm_settings_mapping():
    from masterone.config import Settings
    from masterone.llm.config import LLMSettings
    s = Settings(llm_enabled=False)
    ls = LLMSettings(
        enabled=s.llm_enabled,
        semantic_enabled=s.llm_semantic_enabled,
        explanation_enabled=s.llm_explanation_enabled,
        openrouter_api_key=s.openrouter_api_key,
        openrouter_base_url=s.openrouter_base_url,
        semantic_model=s.llm_semantic_model,
        explanation_model=s.llm_explanation_model,
        semantic_batch_size=s.llm_semantic_batch_size,
        max_concurrency=s.llm_max_concurrency,
        request_timeout_seconds=s.llm_request_timeout_seconds,
        max_retries=s.llm_max_retries,
        cache_ttl_seconds=s.llm_cache_ttl_seconds,
        allow_auto_merge_evidence=s.llm_allow_auto_merge_evidence,
        max_semantic_calls_per_run=s.llm_max_semantic_calls_per_run,
        max_explanation_calls_per_run=s.llm_max_explanation_calls_per_run,
        max_input_tokens_per_request=s.llm_max_input_tokens_per_request,
        max_output_tokens_semantic=s.llm_max_output_tokens_semantic,
        max_output_tokens_explanation=s.llm_max_output_tokens_explanation,
    )
    assert ls.enabled is False
    assert ls.semantic_model == "google/gemini-2.5-flash"


# ── TASK-7.3: Config fail-fast ────────────────────────────────────────────────

def test_production_config_fails_without_required_fields():
    from pydantic import ValidationError
    from masterone.config import Settings
    with pytest.raises((ValidationError, ValueError)):
        Settings(app_env="production", database_url="", auth_csrf_secret="")


def test_production_config_passes_with_required_fields():
    from masterone.config import Settings
    s = Settings(
        app_env="production",
        database_url="postgresql+psycopg://user:pass@db/test",
        auth_csrf_secret="a" * 32,
    )
    assert s.app_env == "production"


def test_development_config_passes_without_required_fields():
    from masterone.config import Settings
    s = Settings(app_env="development", database_url="", auth_csrf_secret="")
    assert s.app_env == "development"


# ── TASK-2.1: Gov ID blocking ─────────────────────────────────────────────────

def _make_record(id_: str, gov_id: str = "", phone: str = "", name: str = "test") -> dict:
    quality = {}
    if gov_id:
        quality["government_id"] = "VALID"
    if phone:
        quality["phone"] = "VALID"
    return {
        "id": id_,
        "normalized_fields": {
            "name": name,
            "phone": phone,
            "phones": [{"value": phone, "quality": "VALID"}] if phone else [],
            "email": "",
            "emails": [],
            "government_id": gov_id,
            "_field_quality": quality,
        },
    }


def test_blocking_on_gov_id():
    from masterone.mdm.pipeline.blocking import generate_candidates
    r1 = _make_record("r1", gov_id="123456789")
    r2 = _make_record("r2", gov_id="123456789")
    r3 = _make_record("r3", gov_id="999888777")
    candidates = generate_candidates([r1, r2, r3])
    assert ("r1", "r2") in candidates
    blocking_reasons = candidates[("r1", "r2")]
    assert "exact_gov_id" in blocking_reasons


def test_blocking_on_gov_id_not_blocked_when_invalid():
    from masterone.mdm.pipeline.blocking import generate_candidates
    r1 = {
        "id": "r1",
        "normalized_fields": {
            "name": "test",
            "phone": "",
            "phones": [],
            "email": "",
            "emails": [],
            "government_id": "123456789",
            "_field_quality": {"government_id": "INVALID"},
        },
    }
    r2 = {
        "id": "r2",
        "normalized_fields": {
            "name": "test",
            "phone": "",
            "phones": [],
            "email": "",
            "emails": [],
            "government_id": "123456789",
            "_field_quality": {"government_id": "INVALID"},
        },
    }
    candidates = generate_candidates([r1, r2])
    # Both invalid → should NOT block on gov_id
    if ("r1", "r2") in candidates:
        assert "exact_gov_id" not in candidates[("r1", "r2")]


# ── TASK-2.2: Composite blocking keys ────────────────────────────────────────

def test_composite_blocking_surname_year():
    from masterone.mdm.pipeline.blocking import generate_candidates
    r1 = {
        "id": "r1",
        "normalized_fields": {
            "name": "nguyen van an",
            "phones": [],
            "emails": [],
            "phone": "",
            "email": "",
            "_field_quality": {},
            "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
            "dob": "1990-01-01",
            "address_parts": {"province": "HN"},
        },
    }
    r2 = {
        "id": "r2",
        "normalized_fields": {
            "name": "nguyen van an",
            "phones": [],
            "emails": [],
            "phone": "",
            "email": "",
            "_field_quality": {},
            "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
            "dob": "1990-05-15",
            "address_parts": {"province": "HN"},
        },
    }
    candidates = generate_candidates([r1, r2])
    assert ("r1", "r2") in candidates
    reasons = candidates[("r1", "r2")]
    # Should be blocked by surname+year or name_province
    assert reasons & {"surname_year", "name_province", "exact_name"}


# ── TASK-3.1: Verified identity hard constraint §11.4 ─────────────────────────

def test_verified_phone_conflict_fires():
    from masterone.mdm.pipeline.hard_constraints import check_verified_identity
    left_nf = {"phone": "0901234567", "_phone_verified": True}
    right_nf = {"phone": "0907654321", "_phone_verified": True}
    result = check_verified_identity(left_nf, right_nf)
    assert result is not None
    assert result.constraint == "VERIFIED_IDENTITY_CONFLICT"
    assert "PHONE" in result.reason


def test_verified_phone_match_does_not_fire():
    from masterone.mdm.pipeline.hard_constraints import check_verified_identity
    left_nf = {"phone": "0901234567", "_phone_verified": True}
    right_nf = {"phone": "0901234567", "_phone_verified": True}
    assert check_verified_identity(left_nf, right_nf) is None


def test_unverified_phone_conflict_does_not_fire():
    from masterone.mdm.pipeline.hard_constraints import check_verified_identity
    left_nf = {"phone": "0901234567"}
    right_nf = {"phone": "0907654321"}
    assert check_verified_identity(left_nf, right_nf) is None


def test_one_side_verified_does_not_fire():
    from masterone.mdm.pipeline.hard_constraints import check_verified_identity
    left_nf = {"phone": "0901234567", "_phone_verified": True}
    right_nf = {"phone": "0907654321"}
    assert check_verified_identity(left_nf, right_nf) is None


def test_verified_email_conflict_fires():
    from masterone.mdm.pipeline.hard_constraints import check_verified_identity
    left_nf = {"email": "a@b.com", "_email_verified": True}
    right_nf = {"email": "c@d.com", "_email_verified": True}
    result = check_verified_identity(left_nf, right_nf)
    assert result is not None
    assert "EMAIL" in result.reason


def test_check_all_includes_verified_identity():
    from masterone.mdm.pipeline.hard_constraints import check_all
    left = {
        "id": "l1", "entity_type": "PERSON",
        "normalized_fields": {
            "phone": "0901234567", "_phone_verified": True,
            "_field_quality": {"government_id": "MISSING"},
        },
    }
    right = {
        "id": "r1", "entity_type": "PERSON",
        "normalized_fields": {
            "phone": "0907654321", "_phone_verified": True,
            "_field_quality": {"government_id": "MISSING"},
        },
    }
    result = check_all(left, right)
    assert result is not None
    assert result.constraint == "VERIFIED_IDENTITY_CONFLICT"


# ── TASK-3.2: Conflict rule versioning ───────────────────────────────────────

def test_score_pair_includes_conflict_rule_version():
    from masterone.mdm.pipeline.matching import score_pair, CONFLICT_RULE_VERSION
    left = {
        "id": "l1", "entity_type": "PERSON",
        "raw_fields": {"name": "Nguyen Van An", "phone": "0901234567", "email": "", "address": ""},
        "normalized_fields": {
            "name": "nguyen van an", "name_unicode": "nguyen van an",
            "phone": "0901234567",
            "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
            "email": "", "emails": [],
            "address": "", "address_parts": {},
            "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                               "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
            "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
        },
        "quality_flags": [],
    }
    right = {
        "id": "r1", "entity_type": "PERSON",
        "raw_fields": {"name": "Nguyen Van An", "phone": "0901234567", "email": "", "address": ""},
        "normalized_fields": {
            "name": "nguyen van an", "name_unicode": "nguyen van an",
            "phone": "0901234567",
            "phones": [{"value": "0901234567", "quality": "VALID", "is_primary": True}],
            "email": "", "emails": [],
            "address": "", "address_parts": {},
            "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                               "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
            "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": "an"},
        },
        "quality_flags": [],
    }
    result = score_pair(left, right, {"exact_phone"})
    assert "conflict_rule_version" in result
    assert result["conflict_rule_version"] == CONFLICT_RULE_VERSION


# ── TASK-7.1: ObjectStore ABC ─────────────────────────────────────────────────

def test_object_store_abc_exists():
    from masterone.storage.object_store import ObjectStore, R2ObjectStore, LocalObjectStore
    from abc import ABC
    assert issubclass(ObjectStore, ABC)
    assert issubclass(R2ObjectStore, ObjectStore)
    assert issubclass(LocalObjectStore, ObjectStore)


def test_local_object_store_roundtrip(tmp_path):
    from masterone.storage.object_store import LocalObjectStore
    store = LocalObjectStore(tmp_path / "store")
    store.upload_bytes(b"hello world", "test/data.bin")
    assert store.exists("test/data.bin")
    data = store.download_bytes("test/data.bin")
    assert data == b"hello world"
    store.delete("test/data.bin")
    assert not store.exists("test/data.bin")


def test_local_object_store_write_manifest(tmp_path):
    from masterone.storage.object_store import LocalObjectStore
    import json
    store = LocalObjectStore(tmp_path / "store")
    payload = {"version": "1", "stage": "SCORE", "records": 100}
    store.write_manifest("manifests/test.json", payload)
    data = json.loads(store.download_bytes("manifests/test.json").decode())
    assert data["stage"] == "SCORE"


def test_local_object_store_path_traversal_blocked(tmp_path):
    from masterone.storage.object_store import LocalObjectStore
    store = LocalObjectStore(tmp_path / "store")
    with pytest.raises(ValueError):
        store.upload_bytes(b"hack", "../../etc/passwd")


# ── TASK-6.1: PII Masking ─────────────────────────────────────────────────────

def test_pii_masking_viewer_phone():
    from masterone.mdm.pii_masking import mask_phone
    assert mask_phone("0901234567", "VIEWER") == "***567"
    assert mask_phone("", "VIEWER") == ""


def test_pii_masking_steward_full():
    from masterone.mdm.pii_masking import mask_phone
    assert mask_phone("0901234567", "STEWARD") == "0901234567"


def test_pii_masking_email_viewer():
    from masterone.mdm.pii_masking import mask_email
    result = mask_email("nguyen.van.an@gmail.com", "VIEWER")
    assert result.startswith("n***@")
    assert "gmail.com" in result


def test_pii_masking_gov_id_viewer():
    from masterone.mdm.pii_masking import mask_gov_id
    assert mask_gov_id("079123456789", "VIEWER") == "***789"


def test_pii_masking_normalized_fields():
    from masterone.mdm.pii_masking import mask_normalized_fields
    nf = {
        "phone": "0901234567",
        "phones": [{"value": "0901234567", "quality": "VALID"}],
        "email": "a@b.com",
        "emails": [{"value": "a@b.com", "quality": "VALID"}],
        "government_id": "079123456789",
        "name": "Nguyen Van An",
    }
    masked = mask_normalized_fields(nf, "VIEWER")
    assert masked["phone"] == "***567"
    assert masked["phones"][0]["value"] == "***567"
    assert masked["email"].startswith("a***@")
    assert masked["government_id"] == "***789"
    # Name should NOT be masked
    assert masked["name"] == "Nguyen Van An"


def test_pii_masking_senior_steward_full():
    from masterone.mdm.pii_masking import mask_normalized_fields
    nf = {"phone": "0901234567", "email": "a@b.com", "government_id": "123456789"}
    for role in ("STEWARD", "SENIOR_STEWARD", "POLICY_ADMIN", "AUDITOR"):
        masked = mask_normalized_fields(nf, role)
        assert masked["phone"] == "0901234567"
        assert masked["email"] == "a@b.com"
