"""
routers/tiles.py
----------------
Vector Tile (MVT / Protobuf) endpoint for the SIGMAC heatmap layer.

The PostGIS function ``sigmac.fn_mvt_tile(z, x, y, tipo, severidad_min)``
is expected to return a single BYTEA column containing the Mapbox Vector
Tile payload (or an empty byte string when no features fall in the tile).

Authentication is intentionally omitted — tiles are consumed directly by
Leaflet / MapboxGL and must be publicly accessible.
"""

from __future__ import annotations

import math
from typing import Optional

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/tiles", tags=["tiles"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ZOOM = 22
_MVT_MEDIA_TYPE = "application/x-protobuf"
_CACHE_MAX_AGE = 30  # seconds — tiles are near-real-time

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_xyz(z: int, x: int, y: int) -> None:
    """Raise HTTP 400 if tile coordinates are out of range for zoom level z."""
    if not (0 <= z <= _MAX_ZOOM):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zoom z={z} out of range [0, {_MAX_ZOOM}].",
        )
    max_tile = (1 << z) - 1  # 2^z - 1
    if not (0 <= x <= max_tile):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tile x={x} out of range [0, {max_tile}] for z={z}.",
        )
    if not (0 <= y <= max_tile):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tile y={y} out of range [0, {max_tile}] for z={z}.",
        )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/{z}/{x}/{y}.pbf",
    summary="Vector tile MVT",
    description=(
        "Devuelve un tile en formato Mapbox Vector Tile (Protobuf). "
        "Sin autenticación — consumido directamente por el cliente de mapa."
    ),
    responses={
        200: {
            "content": {"application/x-protobuf": {}},
            "description": "Tile MVT con features.",
        },
        204: {"description": "Tile vacío — ningún feature en esta celda."},
        400: {"description": "Coordenadas de tile inválidas."},
    },
)
async def get_mvt_tile(
    z: int,
    x: int,
    y: int,
    tipo: Optional[str] = Query(
        default=None,
        description="Filtro por tipo de incidente (ej. ACCIDENTE, ROBO).",
        max_length=64,
    ),
    severidad_min: int = Query(
        default=1,
        ge=1,
        le=5,
        description="Severidad mínima a incluir (1–5).",
    ),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    _validate_xyz(z, x, y)

    log = logger.bind(z=z, x=x, y=y, tipo=tipo, severidad_min=severidad_min)

    try:
        row = await conn.fetchrow(
            "SELECT sigmac.fn_mvt_tile($1, $2, $3, $4, $5) AS tile",
            z,
            x,
            y,
            tipo,          # NULL-safe — PostGIS function handles None
            severidad_min,
        )
    except asyncpg.PostgresError as exc:
        log.error("MVT query failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al generar tile vectorial.",
        ) from exc

    mvt_bytes: bytes = row["tile"] if row and row["tile"] else b""

    if not mvt_bytes:
        log.debug("Empty tile — returning 204")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    headers = {
        "Cache-Control": f"public, max-age={_CACHE_MAX_AGE}",
        "Content-Encoding": "gzip",  # PostGIS fn_mvt_tile returns gzip-compressed MVT
        "Access-Control-Allow-Origin": "*",
    }

    log.debug("Tile served", size_bytes=len(mvt_bytes))
    return Response(
        content=mvt_bytes,
        media_type=_MVT_MEDIA_TYPE,
        headers=headers,
    )
