"""
clustering.py
-------------
DBSCAN-based incident clustering and anomaly detection for SIGMAC Bolivia.

Spatial distances are computed with the haversine metric so that the eps
parameter has a consistent meaning in metres regardless of latitude.

Coordinate convention
---------------------
* Input  : lon/lat in degrees (EPSG:4326)
* DBSCAN : radians (required by sklearn haversine metric)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import numpy as np
import structlog
from sklearn.cluster import DBSCAN

logger = structlog.get_logger(__name__)

_EARTH_RADIUS_M: float = 6_371_000.0


def _metres_to_radians(metres: float) -> float:
    """Convert a distance in metres to radians on the Earth's surface."""
    return metres / _EARTH_RADIUS_M


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Return the haversine great-circle distance in metres between two points."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


class IncidentClusterer:
    """DBSCAN spatial clustering for incident anomaly detection.

    Parameters
    ----------
    eps_meters:
        Maximum neighbourhood radius in metres.
    min_samples:
        Minimum number of points to form a dense region (core point).
    """

    def __init__(self, eps_meters: float = 300.0, min_samples: int = 5) -> None:
        self.eps_meters = eps_meters
        self.min_samples = min_samples
        self._eps_rad: float = _metres_to_radians(eps_meters)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        lons: np.ndarray,
        lats: np.ndarray,
        timestamps: np.ndarray,
    ) -> np.ndarray:
        """Run DBSCAN and return cluster labels.

        Parameters
        ----------
        lons:
            1-D array of longitudes in degrees.
        lats:
            1-D array of latitudes in degrees.
        timestamps:
            Length-N array of timezone-aware ``datetime`` objects or Unix
            float timestamps (not used for spatial clustering but preserved
            for downstream anomaly scoring).

        Returns
        -------
        np.ndarray
            Integer cluster labels of length N.  ``-1`` indicates noise
            (outlier / isolated anomaly).
        """
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)

        if len(lons) == 0:
            return np.array([], dtype=int)

        # sklearn haversine expects (lat, lon) in radians
        coords_rad = np.column_stack([np.radians(lats), np.radians(lons)])

        db = DBSCAN(
            eps=self._eps_rad,
            min_samples=self.min_samples,
            metric="haversine",
            algorithm="ball_tree",
            n_jobs=-1,
        )
        labels: np.ndarray = db.fit_predict(coords_rad)

        n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = int((labels == -1).sum())
        logger.info(
            "DBSCAN fit complete",
            n_points=len(lons),
            n_clusters=n_clusters,
            n_noise=n_noise,
            eps_m=self.eps_meters,
        )
        return labels

    def detect_anomalies(
        self,
        lons: np.ndarray,
        lats: np.ndarray,
        timestamps: np.ndarray,
        baseline_density: float,
    ) -> List[dict]:
        """Identify anomalous clusters with density significantly above baseline.

        A cluster is flagged as anomalous when its point density exceeds
        ``baseline_density * 3``.

        Parameters
        ----------
        lons, lats:
            Coordinate arrays in degrees.
        timestamps:
            Array of timezone-aware ``datetime`` objects or Unix floats.
        baseline_density:
            Historical baseline incident density (incidents per km²).

        Returns
        -------
        List[dict]
            Each dict describes one anomalous cluster and contains:
            ``centroid_lon``, ``centroid_lat``, ``radius_m``, ``count``,
            ``timestamp_inicio``, ``timestamp_fin``, ``score_anomalia``.
        """
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)

        labels = self.fit(lons, lats, timestamps)
        anomalies: List[dict] = []

        threshold_density = baseline_density * 3.0

        for cluster_id in np.unique(labels):
            if cluster_id == -1:
                continue  # skip noise

            mask = labels == cluster_id
            c_lons = lons[mask]
            c_lats = lats[mask]
            count = int(mask.sum())

            centroid_lon = float(np.mean(c_lons))
            centroid_lat = float(np.mean(c_lats))

            # Compute bounding radius as max distance from centroid
            radius_m = max(
                _haversine_m(centroid_lon, centroid_lat, lo, la)
                for lo, la in zip(c_lons, c_lats)
            ) if count > 1 else self.eps_meters

            # Cluster area in km²
            area_km2 = np.pi * (radius_m / 1000.0) ** 2 if radius_m > 0 else 1e-6
            cluster_density = count / area_km2  # incidents per km²

            if cluster_density <= threshold_density:
                continue

            # Temporal bounds
            ts_values = _extract_epoch_array(timestamps[mask])
            ts_inicio = datetime.fromtimestamp(float(ts_values.min()), tz=timezone.utc).isoformat()
            ts_fin = datetime.fromtimestamp(float(ts_values.max()), tz=timezone.utc).isoformat()

            # Anomaly score: ratio of cluster density to baseline (capped at 10 for normalisation)
            raw_ratio = cluster_density / max(baseline_density, 1e-9)
            score = float(min(raw_ratio / 10.0, 1.0))

            anomalies.append(
                {
                    "cluster_id": int(cluster_id),
                    "centroid_lon": round(centroid_lon, 6),
                    "centroid_lat": round(centroid_lat, 6),
                    "radius_m": round(radius_m, 1),
                    "count": count,
                    "density_incidents_per_km2": round(cluster_density, 4),
                    "timestamp_inicio": ts_inicio,
                    "timestamp_fin": ts_fin,
                    "score_anomalia": round(score, 4),
                }
            )

        anomalies.sort(key=lambda x: x["score_anomalia"], reverse=True)
        logger.info("Anomaly detection complete", n_anomalies=len(anomalies))
        return anomalies


class BaselineCalculator:
    """Computes historical incident density baselines from the SIGMAC database.

    The baseline is expressed as **incidents per km²** averaged over the
    requested time window.
    """

    def compute_baseline(
        self,
        conn,
        municipio_id: int,
        tipo_incidente: str,
        window_days: int = 30,
    ) -> float:
        """Query the DB and return average daily incident density for a municipio.

        Parameters
        ----------
        conn:
            Active ``psycopg2`` connection.
        municipio_id:
            Identifier of the target municipality.
        tipo_incidente:
            Incident type string (e.g. ``'accidente_transito'``).
        window_days:
            Look-back window in days.

        Returns
        -------
        float
            Mean incident density in incidents per km² over the window.
            Returns ``0.0`` if no historical data is found.
        """
        sql = """
            SELECT
                COUNT(*)                                           AS total_incidents,
                ST_Area(
                    ST_Transform(m.geom, 32719)
                ) / 1e6                                            AS area_km2
            FROM sigmac.core_incidentes i
            JOIN sigmac.municipios m ON m.id = %s
            WHERE
                i.geom && m.geom
                AND ST_Within(i.geom, m.geom)
                AND i.tipo_incidente = %s
                AND i.timestamp >= NOW() - INTERVAL '%s days'
        """

        try:
            with conn.cursor() as cur:
                cur.execute(sql, (municipio_id, tipo_incidente, window_days))
                row = cur.fetchone()
        except Exception as exc:
            logger.error(
                "BaselineCalculator DB error",
                municipio_id=municipio_id,
                tipo_incidente=tipo_incidente,
                error=str(exc),
            )
            return 0.0

        if row is None or row[1] is None or float(row[1]) == 0:
            logger.warning(
                "No baseline data found",
                municipio_id=municipio_id,
                tipo_incidente=tipo_incidente,
            )
            return 0.0

        total_incidents = float(row[0])
        area_km2 = float(row[1])

        # Density = incidents / (area * days) — incidents per km² per day
        daily_density = total_incidents / (area_km2 * window_days)

        logger.info(
            "Baseline computed",
            municipio_id=municipio_id,
            tipo_incidente=tipo_incidente,
            total_incidents=int(total_incidents),
            area_km2=round(area_km2, 2),
            daily_density=round(daily_density, 6),
        )
        return float(daily_density)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_epoch_array(timestamps) -> np.ndarray:
    """Return a float64 array of Unix timestamps from mixed datetime/float input."""
    result = np.empty(len(timestamps), dtype=float)
    for i, ts in enumerate(timestamps):
        if isinstance(ts, datetime):
            result[i] = ts.timestamp()
        else:
            result[i] = float(ts)
    return result
