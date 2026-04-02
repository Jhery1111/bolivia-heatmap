"""
routers/incidents.py
--------------------
CRUD endpoints for SIGMAC incidents plus departmental statistics.

All endpoints require JWT authentication.

Bolivia bounding box (WGS-84):
    Longitude: -69.645° W to -57.453° W
    Latitude:  -22.898° S to  -9.669° N
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Optional

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from ..auth import CurrentUser
from ..database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["incidents"])

# ---------------------------------------------------------------------------
# Bolivia bounding box constants
# ---------------------------------------------------------------------------

_BOL_MIN_LON = -69.645
_BOL_MAX_LON = -57.453
_BOL_MIN_LAT = -22.898
_BOL_MAX_LAT = -9.669


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class IncidenteCreate(BaseModel):
    """Body schema for manual incident creation."""

    tipo: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Tipo de incidente (ej. ACCIDENTE, ROBO, INCENDIO).",
        examples=["ACCIDENTE"],
    )
    severidad: int = Field(
        ...,
        ge=1,
        le=5,
        description="Severidad del incidente (1=mínima, 5=crítica).",
    )
    lat: float = Field(
        ...,
        ge=_BOL_MIN_LAT,
        le=_BOL_MAX_LAT,
        description="Latitud WGS-84 dentro de Bolivia.",
    )
    lon: float = Field(
        ...,
        ge=_BOL_MIN_LON,
        le=_BOL_MAX_LON,
        description="Longitud WGS-84 dentro de Bolivia.",
    )
    descripcion: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Descripción libre del incidente.",
    )
    sistema_origen: str = Field(
        default="MANUAL",
        max_length=32,
        description="Sistema que registra el incidente.",
    )

    @field_validator("tipo")
    @classmethod
    def upper_tipo(cls, v: str) -> str:
        return v.upper().strip()


class IncidenteOut(BaseModel):
    """Output schema for a single incident record."""

    id: int
    tipo: str
    severidad: int
    lat: float
    lon: float
    descripcion: Optional[str]
    sistema_origen: str
    timestamp: datetime
    departamento: Optional[str] = None


class PaginatedIncidentes(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[IncidenteOut]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/incidentes",
    response_model=IncidenteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear incidente manual",
    description=(
        "Registra un nuevo incidente con coordenadas validadas dentro de "
        "Bolivia y lo publica en la cola Redis para difusión en tiempo real."
    ),
)
async def create_incidente(
    body: IncidenteCreate,
    current_user: CurrentUser,
    conn: asyncpg.Connection = Depends(get_db),
) -> IncidenteOut:
    log = logger.bind(user=current_user.username, tipo=body.tipo, severidad=body.severidad)
    log.info("Creating incident")

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO sigmac.core_incidentes
                (tipo_incidente, severidad, geom, descripcion, sistema_origen, timestamp)
            VALUES (
                $1,
                $2,
                ST_SetSRID(ST_MakePoint($3, $4), 4326),
                $5,
                $6,
                NOW()
            )
            RETURNING
                id,
                tipo_incidente  AS tipo,
                severidad,
                ST_Y(geom)      AS lat,
                ST_X(geom)      AS lon,
                descripcion,
                sistema_origen,
                timestamp
            """,
            body.tipo,
            body.severidad,
            body.lon,
            body.lat,
            body.descripcion,
            body.sistema_origen,
        )
    except asyncpg.PostgresError as exc:
        log.error("DB insert failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al registrar incidente en la base de datos.",
        ) from exc

    incident = IncidenteOut(**dict(row))

    # Publish to Redis for real-time WebSocket broadcast
    await _publish_to_redis(incident, log)

    log.info("Incident created", id=incident.id)
    return incident


async def _publish_to_redis(incident: IncidenteOut, log) -> None:
    """Fire-and-forget publish to Redis PubSub channel."""
    try:
        import redis.asyncio as aioredis
        from ..config import settings

        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        payload = json.dumps(
            {
                "type": "NEW_INCIDENT",
                "data": {
                    "id": incident.id,
                    "lat": incident.lat,
                    "lon": incident.lon,
                    "tipo": incident.tipo,
                    "severidad": incident.severidad,
                    "timestamp": incident.timestamp.isoformat(),
                    "sistema_origen": incident.sistema_origen,
                },
            }
        )
        await r.publish("sigmac:realtime:channel", payload)
        await r.aclose()
    except Exception as exc:
        # Non-fatal — log and continue; the incident is already saved to DB.
        log.warning("Redis publish failed", error=str(exc))


@router.get(
    "/incidentes",
    response_model=PaginatedIncidentes,
    summary="Listar incidentes (paginado)",
    description=(
        "Devuelve incidentes con soporte de paginación y filtros por tipo, "
        "severidad mínima y rango de fechas."
    ),
)
async def list_incidentes(
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500, description="Máximo de resultados."),
    offset: int = Query(default=0, ge=0, description="Desplazamiento para paginación."),
    tipo: Optional[str] = Query(default=None, max_length=64, description="Filtro tipo."),
    severidad_min: int = Query(default=1, ge=1, le=5, description="Severidad mínima."),
    fecha_desde: Optional[datetime] = Query(
        default=None,
        description="Fecha inicio (ISO 8601, ej. 2024-01-01T00:00:00Z).",
    ),
    fecha_hasta: Optional[datetime] = Query(
        default=None,
        description="Fecha fin (ISO 8601).",
    ),
    conn: asyncpg.Connection = Depends(get_db),
) -> PaginatedIncidentes:
    log = logger.bind(user=current_user.username, limit=limit, offset=offset)
    log.info("list_incidentes")

    # Build dynamic WHERE clause
    conditions = ["severidad >= $1"]
    params: list = [severidad_min]
    idx = 2  # next placeholder index

    if tipo:
        conditions.append(f"tipo_incidente = ${idx}")
        params.append(tipo.upper().strip())
        idx += 1
    if fecha_desde:
        conditions.append(f"timestamp >= ${idx}")
        params.append(fecha_desde)
        idx += 1
    if fecha_hasta:
        conditions.append(f"timestamp <= ${idx}")
        params.append(fecha_hasta)
        idx += 1

    where = " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM sigmac.core_incidentes WHERE {where}"
    data_sql = f"""
        SELECT
            id,
            tipo_incidente   AS tipo,
            severidad,
            ST_Y(geom)       AS lat,
            ST_X(geom)       AS lon,
            descripcion,
            sistema_origen,
            timestamp
        FROM sigmac.core_incidentes
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """

    try:
        total: int = await conn.fetchval(count_sql, *params)
        rows = await conn.fetch(data_sql, *params, limit, offset)
    except asyncpg.PostgresError as exc:
        log.error("list_incidentes query failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener incidentes.",
        ) from exc

    items = [IncidenteOut(**dict(r)) for r in rows]
    return PaginatedIncidentes(total=total, limit=limit, offset=offset, items=items)


@router.get(
    "/incidentes/{incidente_id}",
    response_model=IncidenteOut,
    summary="Detalle de un incidente",
)
async def get_incidente(
    incidente_id: int,
    current_user: CurrentUser,
    conn: asyncpg.Connection = Depends(get_db),
) -> IncidenteOut:
    log = logger.bind(user=current_user.username, incidente_id=incidente_id)
    log.info("get_incidente")

    try:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                tipo_incidente  AS tipo,
                severidad,
                ST_Y(geom)      AS lat,
                ST_X(geom)      AS lon,
                descripcion,
                sistema_origen,
                timestamp
            FROM sigmac.core_incidentes
            WHERE id = $1
            """,
            incidente_id,
        )
    except asyncpg.PostgresError as exc:
        log.error("get_incidente query failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener incidente.",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incidente {incidente_id} no encontrado.",
        )

    return IncidenteOut(**dict(row))


@router.get(
    "/stats/departamentos",
    summary="Estadísticas por departamento",
    description=(
        "Retorna conteo de incidentes, promedio de severidad y tipos más frecuentes "
        "agrupados por departamento, usando fn_estadisticas_departamento."
    ),
)
async def stats_departamentos(
    current_user: CurrentUser,
    hours_back: int = Query(
        default=24,
        ge=1,
        le=720,
        description="Ventana temporal en horas (1–720).",
    ),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[dict]:
    log = logger.bind(user=current_user.username, hours_back=hours_back)
    log.info("stats_departamentos")

    try:
        rows = await conn.fetch(
            "SELECT * FROM sigmac.fn_estadisticas_departamento($1::integer)",
            hours_back,
        )
    except asyncpg.PostgresError as exc:
        log.error("fn_estadisticas_departamento failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener estadísticas departamentales.",
        ) from exc

    return [dict(r) for r in rows]
