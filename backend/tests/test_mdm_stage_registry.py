from masterone.storage.mdm_registry import create_mdm_stage_registry


def test_existing_mdm_stages_are_registered_without_algorithm_stages():
    registry = create_mdm_stage_registry("job-1", "fingerprint", {"candidate_limit": 1000}, "test")
    assert [definition.name for definition in registry.definitions()] == [
        "upload", "merge", "normalize", "exact_match", "result",
    ]
    assert registry.get("exact_match").output_uri.endswith("matching/candidates.jsonl")
