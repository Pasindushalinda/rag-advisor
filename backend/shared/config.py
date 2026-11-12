import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        # Blank entries copied from .env.example mean "unset", not "empty string".
        env_ignore_empty=True, extra="allow")

    # Model names are the provider's *deployment* names (infra/main.tf names them "embedding" and "chat").
    embedding_model_endpoint: str = ""
    embedding_model_api_key: SecretStr
    embedding_model_name: str = "embedding"
    chat_model_endpoint: str = ""
    chat_model_api_key: SecretStr
    chat_model_name: str = "chat"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_timeout: int = 60  # hosted instance cold-starts; the client default (5s) times out
    qdrant_collection: str = "filings"
    service_api_key: SecretStr | None = None
    admin_api_key: SecretStr | None = None

    llm_provider: str = "nvidia"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Libraries such as Langfuse read their config from os.environ, not from Settings.
    for key, value in (settings.model_extra or {}).items():
        os.environ.setdefault(key.upper(), str(value))
    return settings
