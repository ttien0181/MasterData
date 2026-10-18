from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DEV_ORIGIN_REGEX = r"http://(?:localhost|127\.0\.0\.1):[1-9][0-9]{0,4}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8101, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    mdm_embedded_worker_enabled: bool = True
    mdm_trace_enabled: bool = True
    mdm_trace_dir: str = "./data/trace"
    mdm_stage_runner_threshold: int = Field(default=10000, ge=0)
    cors_origins: str = "http://localhost:3000"
    auth_csrf_secret: str = ""

    # LLM (see masterone/llm/policy.py: every outbound call is gated there)
    openai_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_max_output_tokens: int = Field(default=512, ge=1, le=8192)
    gemini_max_retries: int = Field(default=1, ge=0, le=3)
    # 0 disables internal reasoning: classification and short explanations do not need it, and with a
    # thinking model the reasoning tokens otherwise consume maxOutputTokens and the JSON is cut off.
    gemini_thinking_budget: int | None = Field(default=0, ge=0, le=32768)
    semantic_address_enabled: bool = False
    decision_semantics_v2_enabled: bool = False
    cluster_safety_v2_enabled: bool = False
    generation_staging_enabled: bool = False
    mdm_engine_v2_enabled: bool = False
    pipeline_resume_enabled: bool = False
    stream_prepare_normalize_enabled: bool = False
    stream_normalize_build_enabled: bool = False
    stream_build_score_enabled: bool = False
    stream_score_decide_enabled: bool = False
    stream_decide_materialize_enabled: bool = False
    stream_partition_rows: int = Field(default=512, ge=1, le=2048)
    stream_partition_bytes: int = Field(default=16 * 1024**2, ge=1024)
    stream_queue_partitions: int = Field(default=4, ge=1, le=64)
    stream_queue_bytes: int = Field(default=64 * 1024**2, ge=1024)
    stream_consumer_workers: int = Field(default=2, ge=1, le=8)
    stream_upload_workers: int = Field(default=2, ge=1, le=8)
    stream_disk_bytes: int = Field(default=2 * 1024**3, ge=32768)
    stream_timeout_seconds: float = Field(default=60, gt=0)
    generation_batch_size: int = Field(default=512, ge=1, le=2048)
    semantic_address_max_pairs_per_run: int = Field(default=100, ge=0, le=1000)
    semantic_address_timeout_seconds: int = Field(default=15, ge=1, le=60)

    # LLM gateway policy. Defaults fail closed: no provider, model or price is approved.
    llm_enabled: bool = False
    llm_explanation_enabled: bool = False
    llm_approved_providers: str = ""  # CSV, subset of: gemini, openrouter
    llm_approved_models: str = ""  # CSV of exact model ids that may be sent
    llm_policy_revision: str = "p119-llm-v1"
    llm_request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    llm_max_concurrency: int = Field(default=5, ge=1, le=32)
    llm_cache_ttl_seconds: int = Field(default=86400, ge=0)
    # Sized for one full address batch (10 pairs with 64-char pair ids) plus fixed prompt overhead.
    llm_max_input_tokens_per_request: int = Field(default=8000, ge=1)
    llm_max_semantic_calls_per_run: int = Field(default=1000, ge=0)
    llm_max_explanation_calls_per_run: int = Field(default=1000, ge=0)
    llm_global_call_cap: int = Field(default=10000, ge=0)
    llm_user_call_cap: int = Field(default=5000, ge=0)
    llm_calls_per_minute: int = Field(default=120, ge=0)
    llm_token_cap: int = Field(default=50_000_000, ge=0)
    llm_cost_cap_microusd: int = Field(default=100_000_000, ge=0)
    llm_consecutive_failure_limit: int = Field(default=3, ge=1)

    # Gemini is primary. OpenRouter is the backup used only after Gemini quota is exhausted.
    gemini_retention_verified: bool = False
    gemini_retention_contract: str = ""
    gemini_token_price_microusd: int = Field(default=0, ge=0)  # 0 = unverified price, denied
    gemini_quota_cooldown_seconds: int = Field(default=900, ge=0)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_retention_verified: bool = False
    openrouter_retention_contract: str = ""
    openrouter_token_price_microusd: int = Field(default=0, ge=0)

    # Database
    mdm_database_profile: Literal["neon", "local"] = "neon"
    database_url: str = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"
    mdm_database_url: str = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"
    mdm_database_url_local: str = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"

    @property
    def active_mdm_database_url(self) -> str:
        """Select local PostgreSQL or the configured hosted PostgreSQL URL."""
        if self.mdm_database_profile == "local":
            return self.mdm_database_url_local
        return self.mdm_database_url

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"

    # Cloudflare R2 object storage (optional — leave empty to disable)
    r2_endpoint_url: str = ""
    # Optional S3 API endpoint reachable by the browser, for presigned URLs.
    # Leave empty for Cloudflare R2 (same endpoint as the server).
    r2_presign_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    export_batch_rows: int = Field(default=512, ge=1, le=2048)
    export_max_group_records: int = Field(default=2048, ge=1, le=10000)
    export_disk_bytes: int = Field(default=2 * 1024**3, ge=32768)
    export_max_rss_bytes: int = Field(default=0, ge=0)
    export_timeout_seconds: float = Field(default=3600, gt=0)
    export_artifact_dir: str = "./data/export-artifacts"
    export_parquet_enabled: bool = False

    # Resumable CSV parts use this directory when object storage is disabled.
    upload_staging_dir: str = "./data/upload-staging"
    # Parallel part downloads from object storage while validating or committing.
    upload_download_workers: int = Field(default=4, ge=1, le=16)
    # Validate and commit run in a child process; False runs them inside the request (tests, debugging).
    import_jobs_inline: bool = False
    import_jobs_concurrency: int = Field(default=2, ge=1, le=8)

    @property
    def r2_enabled(self) -> bool:
        return bool(self.r2_endpoint_url and self.r2_access_key_id
                    and self.r2_secret_access_key and self.r2_bucket)

    @model_validator(mode="after")
    def _select_gemini_key(self) -> "Settings":
        # Existing single-key requests and retry/cost caps remain unchanged.
        # Accept numbered configuration without silently multiplying calls.
        if not self.gemini_api_key:
            self.gemini_api_key = next((key for key in (
                self.gemini_api_key_1, self.gemini_api_key_2, self.gemini_api_key_3
            ) if key), "")
        return self

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if self.app_env == "production":
            missing = []
            if not self.database_url:
                missing.append("DATABASE_URL")
            if not self.auth_csrf_secret:
                missing.append("AUTH_CSRF_SECRET")
            if self.r2_endpoint_url and not (self.r2_access_key_id and self.r2_secret_access_key):
                missing.append("R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY")
            if self.llm_enabled and not (self.gemini_api_key or self.openrouter_api_key):
                missing.append("GEMINI_API_KEY or OPENROUTER_API_KEY (required when LLM_ENABLED=true)")
            if missing:
                raise ValueError(
                    f"Production config missing required fields: {', '.join(missing)}"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
