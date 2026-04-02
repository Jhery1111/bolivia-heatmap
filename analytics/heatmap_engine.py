"""
heatmap_engine.py
-----------------
Orchestration layer for the SIGMAC Bolivia heatmap system.

HeatmapEngine wires together TemporalKDE, IncidentClusterer,
BaselineCalculator, and TopographyAdjuster into a single interface
consumed by the REST/WebSocket layer.

Coordinate convention
---------------------
* All input/output coordinates: WGS 84 / EPSG:4326 (lon, lat degrees)
* Internal metric calculations: UTM Zone 19S / EPSG:32719

Alert levels returned by get_realtime_summary
---------------------------------------------
0 — green   : normal traffic
1 — yellow  : elevated activity
2 — orange  : high incident density
3 — red     : critical / anomalous cluster detected
4 — black   : extreme / multiple simultaneous anomalies
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import structlog

from .kde_temporal import TemporalKDE
from .clustering import IncidentClusterer, BaselineCalculator
from .topography import TopographyAdjuster

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Zoom-level → grid resolution mapping (degrees)
# ---------------------------------------------------------------------------
_ZOOM_RESOLUTION: list[tuple[int, float]] = [
    (8, 0.10),   # z < 8  → 0.10°  (~11 km at equator)
    (12, 0.02),  # z 8–12 → 0.02°  (~2.2 km)
    (99, 0.005), # z > 12 → 0.005° (~550 m)
]


def _resolution_for_zoom(zoom: int) -> float:
    """Return the appropriate grid cell size in degrees for a given zoom level."""
    for threshold, res in _ZOOM_RESOLUTION:
        if zoom < threshold:
            return res
    return _ZOOM_RESOLUTION[-1][1]


def _bbox_centre(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Return the geographic centre (lon, lat) of a bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return (min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0


def _fetch_incidents(
    conn,
    bbox: tuple[float, float, float, float],
    hours_back: int,
    tipo_incidente: Optional[str],
    severidad_min: int,
) -> list[tuple]:
    """Run a parameterised PostGIS query and return raw rows.

    Returns
    -------
    list of (lon, lat, severidad, ts_epoch) tuples.
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    tipo_filter = "AND i.tipo_incidente = %(tipo)s" if tipo_incidente else ""

    sql = f"""
        SELECT
            ST_X(i.geom)                        AS lon,
            ST_Y(i.geom)                        AS lat,
            i.severidad,
            EXTRACT(EPOCH FROM i.timestamp)     AS ts_epoch
        FROM sigmac.core_incidentes i
        WHERE
            i.geom && ST_MakeEnvelope(%(min_lon)s, %(min_lat)s,
                                      %(max_lon)s, %(max_lat)s, 4326)
            AND i.timestamp >= NOW() - (%(hours_back)s || ' hours')::INTERVAL
            AND i.severidad >= %(severidad_min)s
            {tipo_filter}
        ORDER BY i.timestamp DESC
    """

    params: dict = {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
        "hours_back": hours_back,
        "severidad_min": severidad_min,
    }
    if tipo_incidente:
        params["tipo"] = tipo_incidente

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


class HeatmapEngine:
    """Main orchestration engine for the SIGMAC heatmap system.

    Parameters
    ----------
    conn:
        Active ``psycopg2`` database connection.  The caller is responsible
        for connection lifecycle management (open, close, reconnect).
    """

    def __init__(self, conn) -> None:
        self.conn = conn
        self._topo = TopographyAdjuster()
        self._baseline_calc = BaselineCalculator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_heatmap(
        self,
        bbox: tuple[float, float, float, float],
        zoom: int,
        hours_back: int = 24,
        tipo_incidente: Optional[str] = None,
        severidad_min: int = 1,
        bandwidth_m: int = 500,
        decay_halflife_hours: float = 24 * 7,
    ) -> dict:
        """Compute a KDE-based heatmap for the requested bounding box.

        Parameters
        ----------
        bbox:
            ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        zoom:
            Leaflet/Mapbox zoom level — controls grid resolution.
        hours_back:
            How many hours of incident history to include.
        tipo_incidente:
            Optional incident-type filter (``None`` = all types).
        severidad_min:
            Minimum severity to include (1–5, default 1 = all).
        bandwidth_m:
            Base KDE bandwidth in metres *before* topographic adjustment.
        decay_halflife_hours:
            Temporal decay half-life in hours.

        Returns
        -------
        dict
            GeoJSON ``FeatureCollection`` with one ``Point`` feature per
            non-zero grid cell.  Each feature carries a ``density`` property
            in ``[0, 1]``.  A ``metadata`` key is also included.
        """
        rows = _fetch_incidents(
            self.conn, bbox, hours_back, tipo_incidente, severidad_min
        )

        if not rows:
            logger.warning("compute_heatmap: no incidents in bbox", bbox=bbox)
            return {
                "type": "FeatureCollection",
                "features": [],
                "metadata": {
                    "bbox": list(bbox),
                    "hours_back": hours_back,
                    "n_incidents": 0,
                    "computed_at": datetime.now(timezone.utc).isoformat(),
                },
            }

        lons = np.array([r[0] for r in rows], dtype=float)
        lats = np.array([r[1] for r in rows], dtype=float)
        severities = np.array([r[2] for r in rows], dtype=float)
        ts_epochs = np.array([r[3] for r in rows], dtype=float)

        # Topographic bandwidth adjustment at the centre of the bbox
        centre_lon, centre_lat = _bbox_centre(bbox)
        adj_bandwidth = self._topo.adjust_bandwidth(bandwidth_m, centre_lon, centre_lat)

        logger.info(
            "Bandwidth after topographic adjustment",
            base_m=bandwidth_m,
            adjusted_m=round(adj_bandwidth, 1),
            centre_lon=round(centre_lon, 4),
            centre_lat=round(centre_lat, 4),
        )

        # Fit KDE
        points = np.column_stack([lons, lats])
        now = datetime.now(timezone.utc)
        kde = TemporalKDE(
            bandwidth_meters=adj_bandwidth,
            decay_halflife_hours=decay_halflife_hours,
        )
        kde.fit(points, ts_epochs, severities, reference_time=now)

        # Build evaluation grid
        grid_res = _resolution_for_zoom(zoom)
        min_lon, min_lat, max_lon, max_lat = bbox
        grid_lons = np.arange(min_lon, max_lon, grid_res)
        grid_lats = np.arange(min_lat, max_lat, grid_res)

        density_2d = kde.score_grid(grid_lons, grid_lats)

        # Normalise to [0, 1]
        d_min, d_max = density_2d.min(), density_2d.max()
        if d_max > d_min:
            density_norm = (density_2d - d_min) / (d_max - d_min)
        else:
            density_norm = np.zeros_like(density_2d)

        # Encode as GeoJSON FeatureCollection — omit zero-density cells
        features: list[dict] = []
        for lat_idx, lat_val in enumerate(grid_lats):
            for lon_idx, lon_val in enumerate(grid_lons):
                d = float(density_norm[lat_idx, lon_idx])
                if d == 0.0:
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [
                                round(float(lon_val), 6),
                                round(float(lat_val), 6),
                            ],
                        },
                        "properties": {"density": round(d, 6)},
                    }
                )

        logger.info(
            "compute_heatmap complete",
            n_incidents=len(rows),
            n_grid_points=len(features),
            zoom=zoom,
            grid_res=grid_res,
            adj_bandwidth_m=round(adj_bandwidth, 1),
        )

        return {
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "bbox": list(bbox),
                "hours_back": hours_back,
                "zoom": zoom,
                "grid_resolution_deg": grid_res,
                "bandwidth_base_m": bandwidth_m,
                "bandwidth_adjusted_m": round(adj_bandwidth, 1),
                "n_incidents": len(rows),
                "tipo_incidente": tipo_incidente,
                "severidad_min": severidad_min,
                "computed_at": now.isoformat(),
            },
        }

    def compute_anomalies(
        self,
        bbox: tuple[float, float, float, float],
        hours_back: int = 6,
        eps_meters: float = 300.0,
        min_samples: int = 5,
        municipio_id: Optional[int] = None,
        tipo_incidente: Optional[str] = None,
    ) -> List[dict]:
        """Detect spatially anomalous incident clusters within a bounding box.

        Clusters whose density exceeds three times the historical baseline are
        flagged as anomalies.

        Parameters
        ----------
        bbox:
            ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        hours_back:
            Lookback window for recent incident data (default 6 h).
        eps_meters:
            DBSCAN neighbourhood radius in metres.
        min_samples:
            DBSCAN minimum cluster size.
        municipio_id:
            If provided, the historical baseline is fetched for this
            municipality.  ``None`` uses a fallback value of 0.01.
        tipo_incidente:
            Optional incident-type filter for both recent data and baseline.

        Returns
        -------
        List[dict]
            Anomaly records sorted by ``score_anomalia`` descending.
        """
        rows = _fetch_incidents(
            self.conn, bbox, hours_back, tipo_incidente, severidad_min=1
        )

        if not rows:
            logger.info("compute_anomalies: no recent incidents", bbox=bbox)
            return []

        lons = np.array([r[0] for r in rows], dtype=float)
        lats = np.array([r[1] for r in rows], dtype=float)
        ts_epochs = np.array([r[3] for r in rows], dtype=float)

        # Historical baseline
        if municipio_id is not None and tipo_incidente is not None:
            baseline = self._baseline_calc.compute_baseline(
                self.conn, municipio_id, tipo_incidente
            )
        else:
            baseline = 0.01  # fallback: ~1 incident per 100 km²

        clusterer = IncidentClusterer(
            eps_meters=eps_meters, min_samples=min_samples
        )
        anomalies = clusterer.detect_anomalies(
            lons, lats, ts_epochs, baseline_density=baseline
        )

        logger.info(
            "compute_anomalies complete",
            n_incidents=len(rows),
            n_anomalies=len(anomalies),
            baseline=baseline,
            bbox=bbox,
        )
        return anomalies

    def get_realtime_summary(
        self,
        bbox: tuple[float, float, float, float],
        hours_back: int = 1,
        anomaly_hours_back: int = 6,
    ) -> dict:
        """Return a concise real-time summary of incident activity in a bbox.

        The summary is intended for dashboard widgets and push notifications.
        It is deliberately cheap: one DB query, light KDE scoring at a single
        point, and a DBSCAN pass on the recent window.

        Alert level thresholds
        ----------------------
        0  count == 0
        1  count > 0 and no anomalies
        2  count > 10 or max density > 0.5
        3  any anomaly with score_anomalia >= 0.5
        4  anomalies >= 3 or any score_anomalia >= 0.9

        Parameters
        ----------
        bbox:
            ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
        hours_back:
            Lookback for incident count and primary hotspot (default 1 h).
        anomaly_hours_back:
            Lookback for anomaly detection (default 6 h).

        Returns
        -------
        dict
            Keys: ``count``, ``hotspot`` (GeoJSON point or None),
            ``alert_level`` (0–4), ``anomaly_count``, ``computed_at``.
        """
        now = datetime.now(timezone.utc)

        # --- Recent incident count ---
        rows = _fetch_incidents(
            self.conn, bbox, hours_back, tipo_incidente=None, severidad_min=1
        )
        count = len(rows)

        # --- Hotspot: highest-density grid point (coarse grid) ---
        hotspot: Optional[dict] = None
        max_density: float = 0.0

        if count >= 2:
            lons = np.array([r[0] for r in rows], dtype=float)
            lats = np.array([r[1] for r in rows], dtype=float)
            severities = np.array([r[2] for r in rows], dtype=float)
            ts_epochs = np.array([r[3] for r in rows], dtype=float)

            centre_lon, centre_lat = _bbox_centre(bbox)
            adj_bw = self._topo.adjust_bandwidth(500, centre_lon, centre_lat)

            kde = TemporalKDE(bandwidth_meters=adj_bw, decay_halflife_hours=6.0)
            try:
                kde.fit(
                    np.column_stack([lons, lats]),
                    ts_epochs,
                    severities,
                    reference_time=now,
                )
                # Evaluate at each incident location to find the hotspot centre
                densities = kde.score_points(lons, lats)
                best_idx = int(np.argmax(densities))
                d_min, d_max = densities.min(), densities.max()
                if d_max > d_min:
                    max_density = float(
                        (densities[best_idx] - d_min) / (d_max - d_min)
                    )
                hotspot = {
                    "type": "Point",
                    "coordinates": [
                        round(float(lons[best_idx]), 6),
                        round(float(lats[best_idx]), 6),
                    ],
                    "density": round(max_density, 4),
                }
            except Exception as exc:
                logger.warning("Hotspot KDE failed", error=str(exc))

        # --- Anomaly detection ---
        anomalies = self.compute_anomalies(bbox, hours_back=anomaly_hours_back)
        anomaly_count = len(anomalies)
        max_anomaly_score = (
            max(a["score_anomalia"] for a in anomalies) if anomalies else 0.0
        )

        # --- Alert level ---
        alert_level = _compute_alert_level(
            count=count,
            max_density=max_density,
            anomaly_count=anomaly_count,
            max_anomaly_score=max_anomaly_score,
        )

        summary = {
            "count": count,
            "hotspot": hotspot,
            "alert_level": alert_level,
            "anomaly_count": anomaly_count,
            "max_anomaly_score": round(max_anomaly_score, 4),
            "bbox": list(bbox),
            "hours_back": hours_back,
            "computed_at": now.isoformat(),
        }

        logger.info(
            "get_realtime_summary",
            count=count,
            alert_level=alert_level,
            anomaly_count=anomaly_count,
        )
        return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_alert_level(
    count: int,
    max_density: float,
    anomaly_count: int,
    max_anomaly_score: float,
) -> int:
    """Map incident metrics to a discrete alert level 0–4.

    Parameters
    ----------
    count:
        Number of incidents in the lookback window.
    max_density:
        Normalised peak KDE density in [0, 1].
    anomaly_count:
        Number of detected anomalous clusters.
    max_anomaly_score:
        Highest anomaly score in [0, 1].

    Returns
    -------
    int
        Alert level in range [0, 4].
    """
    if count == 0:
        return 0
    if anomaly_count >= 3 or max_anomaly_score >= 0.9:
        return 4
    if max_anomaly_score >= 0.5:
        return 3
    if count > 10 or max_density > 0.5:
        return 2
    return 1
