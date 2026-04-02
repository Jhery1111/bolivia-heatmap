"""
config.py
---------
Centralised application settings loaded from environment variables / .env file.
All settings are validated and typed by Pydantic Settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = "postgresql://sigmac:sigmac@localhost:5432/sigmac"

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # ------------------------------------------------------------------
    # JWT / Auth
    # ------------------------------------------------------------------
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
    ]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> List[str]:
        """Accept a comma-separated string OR a real list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    APP_VERSION: str = "1.0.0"
    APP_TITLE: str = "SIGMAC API — DNTT Bolivia"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()


# Convenient module-level alias consumed by the rest of the codebase.
settings: Settings = get_settings()
