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

    # LLM
    openai_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

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
