from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    app_port: int = Field(default=8001, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"
    auth_csrf_secret: str = ""

    # LLM
    openai_api_key: str = ""
    model_name: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    semantic_address_enabled: bool = False
    semantic_address_max_pairs_per_run: int = Field(default=100, ge=0, le=1000)
    semantic_address_timeout_seconds: int = Field(default=15, ge=1, le=60)

    # Database
    database_url: str = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"
    mdm_database_url: str = "postgresql+psycopg://masterone:masterone@localhost:5432/masterone"

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"

    # Cloudflare R2 object storage (optional — leave empty to disable)
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    @property
    def r2_enabled(self) -> bool:
        return bool(self.r2_endpoint_url and self.r2_access_key_id
                    and self.r2_secret_access_key and self.r2_bucket)


@lru_cache
def get_settings() -> Settings:
    return Settings()
