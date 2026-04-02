"""
routers/heatmap.py
------------------
Endpoints for heatmap computation, anomaly detection, real-time summary,
and proximity incident queries.  All endpoints require JWT authentication.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..auth import CurrentUser, get_current_user
from ..database import get_db, get_sync_db

logger = structlog.get_logger(__name__)

# One thread-pool shared across all heatmap requests.
# The HeatmapEngine is CPU/IO-bound (NumPy + psycopg2) so we run it in a
# thread executor to avoid blocking the asyncio event loop.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="heatmap")

router = APIRouter(prefix="/api/v1", tags=["heatmap"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BboxQuery = Annotated[
    str,
    Query(
        description=(
            "Bounding box en WGS-84: minLon,minLat,maxLon,maxLat "
            "(ej. -69.5,-22.9,-57.3,-9.7 para Bolivia completa)"
        ),
        pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?$",
    ),
]


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """Parse and validate a comma-separated bbox string."""
    try:
        parts = [float(v) for v in raw.split(",")]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bbox debe ser minLon,minLat,maxLon,maxLat con valores numéricos.",
        ) from exc

    if len(parts) != 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bbox debe contener exactamente 4 valores.",
        )

    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bbox inválido: minLon debe ser < maxLon y minLat < maxLat.",
        )
    return min_lon, min_lat, max_lon, max_lat


async def _run_sync(fn, *args):
    """Execute a blocking callable in the thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, partial(fn, *args))


# ---------------------------------------------------------------------------
# Response models (light wrappers — HeatmapEngine returns plain dicts)
# ---------------------------------------------------------------------------

class HeatmapResponse(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    type: str
    features: list
    metadata: dict


class SummaryResponse(BaseModel):
    count: int
    hotspot: Optional[dict]
    alert_level: int
    anomaly_count: int
    max_anomaly_score: float
    bbox: list
    hours_back: int
    computed_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/heatmap",
    summary="Mapa de calor KDE",
    description=(
        "Computa un mapa de calor KDE ponderado por severidad y temporal "
        "para la bbox y ventana temporal indicadas."
    ),
)
async def get_heatmap(
    current_user: CurrentUser,
    bbox: BboxQuery = Query(...),
    zoom: int = Query(default=10, ge=1, le=18, description="Zoom Leaflet (1–18)."),
    hours_back: int = Query(
        default=24, ge=1, le=168, description="Horas hacia atrás (1–168)."
    ),
    tipo: Optional[str] = Query(
        default=None, max_length=64, description="Filtro tipo incidente."
    ),
    severidad_min: int = Query(
        default=1, ge=1, le=5, description="Severidad mínima (1–5)."
    ),
) -> dict:
    parsed_bbox = _parse_bbox(bbox)
    log = logger.bind(
        user=current_user.username,
        bbox=bbox,
        zoom=zoom,
        hours_back=hours_back,
    )
    log.info("heatmap request")

    def _compute():
        # Import here to avoid circular imports at module load time.
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
        from analytics.heatmap_engine import HeatmapEngine

        with get_sync_db() as conn:
            engine = HeatmapEngine(conn)
            return engine.compute_heatmap(
                bbox=parsed_bbox,
                zoom=zoom,
                hours_back=hours_back,
                tipo_incidente=tipo,
                severidad_min=severidad_min,
            )

    try:
        result = await _run_sync(_compute)
    except Exception as exc:
        log.error("heatmap computation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al computar mapa de calor.",
        ) from exc

    return result


@router.get(
    "/anomalies",
    summary="Detección de anomalías espaciales",
    description="Detecta clusters anómalos usando DBSCAN vs. baseline histórico.",
)
async def get_anomalies(
    current_user: CurrentUser,
    bbox: BboxQuery = Query(...),
    hours_back: int = Query(
        default=6, ge=1, le=168, description="Ventana temporal (1–168 h)."
    ),
    tipo: Optional[str] = Query(
        default=None, max_length=64, description="Filtro tipo incidente."
    ),
) -> list:
    parsed_bbox = _parse_bbox(bbox)
    log = logger.bind(user=current_user.username, bbox=bbox, hours_back=hours_back)
    log.info("anomalies request")

    def _compute():
        from analytics.heatmap_engine import HeatmapEngine

        with get_sync_db() as conn:
            engine = HeatmapEngine(conn)
            return engine.compute_anomalies(
                bbox=parsed_bbox,
                hours_back=hours_back,
                tipo_incidente=tipo,
            )

    try:
        result = await _run_sync(_compute)
    except Exception as exc:
        log.error("anomaly detection failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error en detección de anomalías.",
        ) from exc

    return result


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="Resumen en tiempo real",
    description=(
        "Devuelve un resumen compacto: conteo, hotspot, nivel de alerta y "
        "cantidad de anomalías para la bbox indicada."
    ),
)
async def get_summary(
    current_user: CurrentUser,
    bbox: BboxQuery = Query(...),
) -> dict:
    parsed_bbox = _parse_bbox(bbox)
    log = logger.bind(user=current_user.username, bbox=bbox)
    log.info("summary request")

    def _compute():
        from analytics.heatmap_engine import HeatmapEngine

        with get_sync_db() as conn:
            engine = HeatmapEngine(conn)
            return engine.get_realtime_summary(bbox=parsed_bbox)

    try:
        result = await _run_sync(_compute)
    except Exception as exc:
        log.error("summary computation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener resumen.",
        ) from exc

    return result


@router.get(
    "/incidentes/radio",
    summary="Incidentes en radio geográfico",
    description=(
        "Lista incidentes dentro de `radio_metros` metros del punto (lon, lat) "
        "en las últimas `horas_atras` horas."
    ),
)
async def get_incidentes_radio(
    current_user: CurrentUser,
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitud WGS-84."),
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitud WGS-84."),
    radio_metros: int = Query(
        default=500, ge=100, le=5000, description="Radio de búsqueda (100–5000 m)."
    ),
    horas_atras: int = Query(
        default=24, ge=1, le=168, description="Ventana temporal (1–168 h)."
    ),
    conn=Depends(get_db),
) -> list:
    """Return incidents within a geographic radius using PostGIS fn_incidentes_en_radio."""
    log = logger.bind(
        user=current_user.username,
        lon=lon,
        lat=lat,
        radio_metros=radio_metros,
        horas_atras=horas_atras,
    )
    log.info("incidentes/radio request")

    try:
        rows = await conn.fetch(
            """
            SELECT * FROM sigmac.fn_incidentes_en_radio(
                $1::double precision,
                $2::double precision,
                $3::integer,
                $4::integer
            )
            """,
            lon,
            lat,
            radio_metros,
            horas_atras,
        )
    except Exception as exc:
        log.error("fn_incidentes_en_radio failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al consultar incidentes en radio.",
        ) from exc

    return [dict(r) for r in rows]
