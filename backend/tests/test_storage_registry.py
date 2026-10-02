from masterone.storage.manifest import StageManifest
from masterone.storage.stage_registry import StageDefinition, StageRegistry


def test_stage_registry_resumes_only_matching_manifest():
    registry = StageRegistry(lambda uri: uri == "r2://jobs/j1/merged/merged.csv")
    registry.register(StageDefinition(
        name="merge",
        input_uri="r2://jobs/j1/raw",
        output_uri="r2://jobs/j1/merged/merged.csv",
        schema={"record_id": "string"},
        fingerprint="input-a",
        config_hash="config-a",
        code_version="code-a",
    ))
    manifest = StageManifest(
        job_id="j1", stage="merge", input_uri="r2://jobs/j1/raw",
        output_uri="r2://jobs/j1/merged/merged.csv", schema={"record_id": "string"},
        input_fingerprint="input-a", config_hash="config-a", code_version="code-a",
        row_count=1, bytes=10,
    )
    assert registry.can_resume("merge", manifest)
    assert not registry.can_resume("merge", StageManifest(**{**manifest.to_dict(), "config_hash": "other"}))
