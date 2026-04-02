"""
models.py — Modelos Pydantic v2 para el pipeline ETL SIGMAC.

Flujo:
  datos externos → IncidenteRaw (validación + normalización)
                 → IncidenteNormalizado (listo para escritura en BD)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

import structlog
from pydantic import (
    UUID4,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

log = structlog.get_logger(__name__)

# ------------------------------------------------------------------ #
# Constantes geográficas — Bolivia en WGS 84 (EPSG:4326)             #
# ------------------------------------------------------------------ #
BOL_LAT_MIN = -23.0
BOL_LAT_MAX = -9.0
BOL_LON_MIN = -70.0
BOL_LON_MAX = -57.0

SistemaOrigen = Literal["JUPITER", "MINERVA", "SIGMAC", "MANUAL"]


# ------------------------------------------------------------------ #
# Geocodificación de fallback (offline, centroide por departamento)   #
# ------------------------------------------------------------------ #
_GEOCODING_FALLBACK: Dict[str, tuple[float, float]] = {
    # (lat, lon) — centroides aproximados de capitales departamentales
    "la paz": (-16.495, -68.133),
    "cochabamba": (-17.393, -66.157),
    "santa cruz": (-17.783, -63.182),
    "oruro": (-17.963, -67.113),
    "potosi": (-19.585, -65.752),
    "chuquisaca": (-19.043, -65.259),
    "tarija": (-21.534, -64.729),
    "beni": (-14.831, -64.900),
    "pando": (-11.026, -68.766),
}


def _geocode_fallback(direccion: str) -> tuple[float, float]:
    """
    Geocodificación offline por coincidencia parcial de nombre de ciudad/dpto.
    Retorna las coordenadas del centroide más cercano encontrado,
    o el centroide nacional de Bolivia si no hay coincidencia.
    """
    addr_lower = direccion.lower()
    for city, coords in _GEOCODING_FALLBACK.items():
        if city in addr_lower:
            log.debug("geocoding_fallback_hit", direccion=direccion, ciudad=city)
            return coords
    # Centroide nacional de Bolivia
    log.warning("geocoding_fallback_national", direccion=direccion)
    return (-16.290154, -63.588653)


# ------------------------------------------------------------------ #
# IncidenteRaw — modelo de entrada                                    #
# ------------------------------------------------------------------ #
class IncidenteRaw(BaseModel):
    """
    Modelo de validación para incidentes provenientes de cualquier fuente.

    Reglas:
    - latitud y longitud deben estar dentro del bbox de Bolivia,
      O ambas pueden ser None si se provee `direccion` para geocodificar.
    - severidad en rango 1-5.
    - timestamp es obligatorio; si viene como string se parsea a datetime UTC.
    """

    id_sistema_origen: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identificador único en el sistema de origen.",
    )
    sistema_origen: SistemaOrigen = Field(
        ...,
        description="Sistema que generó el incidente.",
    )
    tipo_incidente: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Categoría/tipo del incidente.",
    )
    severidad: int = Field(
        ...,
        ge=1,
        le=5,
        description="Severidad del incidente (1=mínima, 5=crítica).",
    )
    timestamp: datetime = Field(
        ...,
        description="Fecha/hora del incidente en UTC.",
    )
    latitud: Optional[float] = Field(
        default=None,
        description="Latitud WGS84. Requerida si no se provee direccion.",
    )
    longitud: Optional[float] = Field(
        default=None,
        description="Longitud WGS84. Requerida si no se provee direccion.",
    )
    direccion: Optional[str] = Field(
        default=None,
        description="Dirección textual (usado para geocodificación de fallback).",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Datos adicionales del sistema de origen.",
    )

    # ---------------------------------------------------------------- #
    # Validadores de campo                                               #
    # ---------------------------------------------------------------- #

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalise_timestamp(cls, v: Any) -> datetime:
        """Acepta datetime, string ISO 8601, o epoch int/float."""
        if isinstance(v, datetime):
            ts = v
        elif isinstance(v, (int, float)):
            ts = datetime.fromtimestamp(v, tz=timezone.utc)
        elif isinstance(v, str):
            from dateutil.parser import parse as dateutil_parse
            ts = dateutil_parse(v)
        else:
            raise ValueError(f"Tipo de timestamp no soportado: {type(v)}")

        # Asegurar que siempre sea timezone-aware UTC
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return ts

    @field_validator("latitud")
    @classmethod
    def validate_latitud(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (BOL_LAT_MIN <= v <= BOL_LAT_MAX):
            raise ValueError(
                f"Latitud {v} fuera del bbox de Bolivia "
                f"({BOL_LAT_MIN} a {BOL_LAT_MAX})."
            )
        return v

    @field_validator("longitud")
    @classmethod
    def validate_longitud(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (BOL_LON_MIN <= v <= BOL_LON_MAX):
            raise ValueError(
                f"Longitud {v} fuera del bbox de Bolivia "
                f"({BOL_LON_MIN} a {BOL_LON_MAX})."
            )
        return v

    # ---------------------------------------------------------------- #
    # Validador de modelo: geocodificación cuando faltan coordenadas     #
    # ---------------------------------------------------------------- #

    @model_validator(mode="after")
    def resolve_coordinates(self) -> "IncidenteRaw":
        """
        Si latitud/longitud son None, intenta geocodificar mediante
        `direccion`. Si tampoco hay direccion, lanza un error.
        """
        if self.latitud is None or self.longitud is None:
            if not self.direccion:
                raise ValueError(
                    "Se requiere (latitud + longitud) o direccion para geocodificar."
                )
            lat, lon = _geocode_fallback(self.direccion)
            object.__setattr__(self, "latitud", lat)
            object.__setattr__(self, "longitud", lon)
            log.info(
                "coordenadas_geocodificadas",
                id_sistema_origen=self.id_sistema_origen,
                direccion=self.direccion,
                lat=lat,
                lon=lon,
            )
        return self


# ------------------------------------------------------------------ #
# IncidenteNormalizado — modelo interno listo para BD                 #
# ------------------------------------------------------------------ #
class IncidenteNormalizado(BaseModel):
    """
    Representación final del incidente, enriquecida con:
    - UUID interno generado
    - geom_wkt: expresión WKT para ST_GeomFromText en PostGIS
    """

    id: UUID4 = Field(
        default_factory=uuid.uuid4,
        description="UUID interno asignado por el ETL.",
    )
    latitud: float
    longitud: float
    geom_wkt: str = Field(
        description="Geometría en WKT para ST_GeomFromText(geom_wkt, 4326).",
    )
    tipo_incidente: str
    severidad: int
    timestamp: datetime
    id_sistema_origen: str
    sistema_origen: SistemaOrigen
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: IncidenteRaw) -> "IncidenteNormalizado":
        """
        Convierte un IncidenteRaw validado a IncidenteNormalizado,
        calculando el WKT de la geometría.

        Usa el formato:  POINT(lon lat)  — orden estándar OGC para PostGIS.
        Equivale a:  ST_SetSRID(ST_MakePoint(longitud, latitud), 4326)
        """
        assert raw.latitud is not None and raw.longitud is not None  # ya validado

        geom_wkt = f"POINT({raw.longitud} {raw.latitud})"

        return cls(
            latitud=raw.latitud,
            longitud=raw.longitud,
            geom_wkt=geom_wkt,
            tipo_incidente=raw.tipo_incidente,
            severidad=raw.severidad,
            timestamp=raw.timestamp,
            id_sistema_origen=raw.id_sistema_origen,
            sistema_origen=raw.sistema_origen,
            metadata=raw.metadata,
        )
