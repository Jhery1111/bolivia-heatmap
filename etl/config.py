"""
config.py — Configuración centralizada del ETL SIGMAC usando pydantic-settings.

Carga variables desde el entorno y/o un archivo .env en el directorio de trabajo.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del motor de ingesta SIGMAC/DNTT."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Base de datos principal (PostgreSQL + PostGIS)                       #
    # ------------------------------------------------------------------ #
    DATABASE_URL: str = Field(
        default="postgresql://sigmac:sigmac@localhost:5432/sigmac",
        description="DSN de conexión a PostgreSQL con PostGIS.",
    )

    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="URL de conexión a Redis (broker de colas y pub/sub).",
    )

    # ------------------------------------------------------------------ #
    # Parámetros de procesamiento                                          #
    # ------------------------------------------------------------------ #
    BATCH_SIZE: int = Field(
        default=500,
        ge=1,
        le=10_000,
        description="Tamaño máximo de lote para inserciones masivas.",
    )
    SYNC_INTERVAL_SECONDS: int = Field(
        default=60,
        ge=5,
        description="Intervalo (segundos) entre ciclos de sincronización.",
    )

    # ------------------------------------------------------------------ #
    # Sistema JÚPITER                                                      #
    # ------------------------------------------------------------------ #
    JUPITER_API_URL: str = Field(
        default="http://jupiter.internal/api/v1",
        description="URL base de la API REST de JÚPITER.",
    )
    JUPITER_API_KEY: str = Field(
        default="CHANGE_ME",
        description="API key de autenticación para JÚPITER.",
    )

    # ------------------------------------------------------------------ #
    # Sistema MINERVA                                                      #
    # ------------------------------------------------------------------ #
    MINERVA_DB_URL: str = Field(
        default="postgresql://minerva:minerva@minerva-db:5432/minerva",
        description="DSN de conexión a la base de datos de MINERVA.",
    )

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        description="Nivel de logging (DEBUG|INFO|WARNING|ERROR|CRITICAL).",
    )


# Instancia global — importar desde aquí en el resto del proyecto.
settings = Settings()
