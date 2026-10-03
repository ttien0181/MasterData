from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8001, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"
    auth_csrf_secret: str = ""

    # LLM — legacy (Gemini explanation, giữ để không break existing code)
    openai_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # OpenRouter — LLM integration (Phase L0)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    llm_enabled: bool = True
    llm_semantic_enabled: bool = True
    llm_explanation_enabled: bool = True

    llm_semantic_model: str = "openai/gpt-5.6-luna"
    llm_explanation_model: str = "openai/gpt-5.4-nano"

    llm_semantic_batch_size: int = Field(default=10, ge=1, le=50)
    llm_max_concurrency: int = Field(default=5, ge=1, le=20)
    llm_request_timeout_seconds: int = Field(default=15, ge=5, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    llm_cache_ttl_seconds: int = Field(default=604800, ge=60)
    llm_allow_auto_merge_evidence: bool = False

    llm_max_semantic_calls_per_run: int = Field(default=2000, ge=0)
    llm_max_explanation_calls_per_run: int = Field(default=500, ge=0)
    llm_max_input_tokens_per_request: int = Field(default=8000, ge=256)
    llm_max_output_tokens_semantic: int = Field(default=500, ge=64)
    llm_max_output_tokens_explanation: int = Field(default=500, ge=64)

    # Database
    database_url: str = "sqlite:///./data/app.db"
    mdm_database_url: str = "sqlite:///./data/masterone.db"
    work_dir: str = "./data/work"
    redis_url: str = ""
    redis_progress_ttl_seconds: int = Field(default=86400, ge=60)
    storage_code_version: str = "mdm-storage-v1"

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"

    # Cloudflare R2 storage
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    large_file_url_ttl_seconds: int = Field(default=900, ge=60, le=3600)


@lru_cache
def get_settings() -> Settings:
    return Settings()
