"""
kde_temporal.py
---------------
Temporal KDE with exponential decay for SIGMAC Bolivia incident density estimation.

Coordinate convention
---------------------
* Storage  : WGS 84 / EPSG:4326  (lon, lat degrees)
* Metric   : UTM Zone 19S / EPSG:32719  (metres)

The KDE itself operates in geographic degrees; the bandwidth supplied in metres
is converted to an approximate degree equivalent using the mean latitude of the
dataset so that scipy.stats.gaussian_kde receives consistent units.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import structlog
from scipy.stats import gaussian_kde

logger = structlog.get_logger(__name__)

# Approximate metres per degree of latitude (constant)
_METRES_PER_DEG_LAT: float = 111_320.0


def _metres_to_degrees(metres: float, ref_lat_deg: float) -> float:
    """Convert a metre distance to approximate decimal degrees at a given latitude.

    Uses the mean of the latitude-dependent longitude scale and the fixed
    latitude scale so the conversion is isotropic in the local area.

    Parameters
    ----------
    metres:
        Distance in metres to convert.
    ref_lat_deg:
        Reference latitude in decimal degrees (used for longitude scaling).

    Returns
    -------
    float
        Approximate degree equivalent.
    """
    lat_deg = metres / _METRES_PER_DEG_LAT
    lon_deg = metres / (_METRES_PER_DEG_LAT * np.cos(np.radians(ref_lat_deg)))
    return float(np.mean([lat_deg, lon_deg]))


class TemporalKDE:
    """Kernel Density Estimator with exponential temporal decay and severity weighting.

    Parameters
    ----------
    bandwidth_meters:
        Spatial bandwidth in metres.  Converted to degrees at fit-time using
        the mean latitude of the training data.
    decay_halflife_hours:
        Half-life in hours for the exponential decay weight.  An event with
        the same severity scored at ``t = halflife`` receives half the weight
        of an event scored at ``t = 0``.  Default is one week (168 h).
    """

    def __init__(
        self,
        bandwidth_meters: float = 500.0,
        decay_halflife_hours: float = 24 * 7,
    ) -> None:
        self.bandwidth_meters = bandwidth_meters
        self.decay_halflife_hours = decay_halflife_hours
        self._lambda: float = np.log(2) / decay_halflife_hours
        self._kde: Optional[gaussian_kde] = None
        self._ref_lat: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        points: np.ndarray,
        timestamps: np.ndarray,
        severities: np.ndarray,
        reference_time: Optional[datetime] = None,
    ) -> "TemporalKDE":
        """Fit the KDE on a set of geo-located, time-stamped incidents.

        Parameters
        ----------
        points:
            Shape ``(N, 2)`` array of ``[longitude, latitude]`` in degrees.
        timestamps:
            Length-N array of ``datetime`` objects (timezone-aware) or
            Unix timestamps (float seconds since epoch).
        severities:
            Length-N integer array of severity scores (1–5).
        reference_time:
            The "now" used to compute temporal deltas.  Defaults to the
            current UTC time.

        Returns
        -------
        self
        """
        points = np.asarray(points, dtype=float)
        severities = np.asarray(severities, dtype=float)

        if points.ndim != 2 or points.shape[1] != 2:
            raise ValueError("points must have shape (N, 2) with columns [lon, lat]")
        if len(points) != len(severities):
            raise ValueError("points and severities must have the same length")

        n = len(points)
        if n < 2:
            raise ValueError("At least 2 data points are required to fit a KDE")

        ref_time = reference_time or datetime.now(timezone.utc)

        # Compute delta_hours for each event
        delta_hours = self._compute_delta_hours(timestamps, ref_time)

        # Temporal-severity weights: w_i = s_i * exp(-lambda * delta_i)
        weights = severities * np.exp(-self._lambda * delta_hours)
        weight_sum = weights.sum()
        if weight_sum == 0:
            raise ValueError("All computed weights are zero — check timestamps and severities")
        weights = weights / weight_sum  # normalise so they sum to 1

        # Reference latitude for bandwidth conversion
        self._ref_lat = float(np.mean(points[:, 1]))
        bw_deg = _metres_to_degrees(self.bandwidth_meters, self._ref_lat)

        # gaussian_kde expects shape (d, N)
        data = points.T  # (2, N)

        # scipy gaussian_kde with explicit bandwidth factor:
        # bw_method='silverman' would ignore our spatial bandwidth, so we pass
        # a scalar factor = desired_bw / data_std.  We compute per-dimension
        # and take the mean to keep things isotropic.
        std_lon = float(np.std(data[0]))
        std_lat = float(np.std(data[1]))
        avg_std = np.mean([std_lon, std_lat]) if (std_lon > 0 and std_lat > 0) else 1e-6
        bw_factor = bw_deg / avg_std if avg_std > 0 else bw_deg

        self._kde = gaussian_kde(data, bw_method=float(bw_factor), weights=weights)

        logger.info(
            "TemporalKDE fitted",
            n_points=n,
            ref_lat=round(self._ref_lat, 4),
            bw_metres=self.bandwidth_meters,
            bw_degrees=round(bw_deg, 6),
        )
        return self

    def score_grid(
        self, grid_lons: np.ndarray, grid_lats: np.ndarray
    ) -> np.ndarray:
        """Evaluate KDE density on a regular grid.

        Parameters
        ----------
        grid_lons:
            1-D array of longitude values.
        grid_lats:
            1-D array of latitude values.

        Returns
        -------
        np.ndarray
            2-D density array of shape ``(len(grid_lats), len(grid_lons))``.
        """
        self._check_fitted()
        lons_2d, lats_2d = np.meshgrid(grid_lons, grid_lats)
        positions = np.vstack([lons_2d.ravel(), lats_2d.ravel()])
        density = self._kde(positions).reshape(lons_2d.shape)
        return density

    def score_points(
        self, lons: np.ndarray, lats: np.ndarray
    ) -> np.ndarray:
        """Evaluate KDE density at arbitrary lon/lat points.

        Parameters
        ----------
        lons:
            1-D array of longitudes.
        lats:
            1-D array of latitudes.

        Returns
        -------
        np.ndarray
            1-D density array of length ``N``.
        """
        self._check_fitted()
        positions = np.vstack([np.asarray(lons), np.asarray(lats)])
        return self._kde(positions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_fitted(self) -> None:
        if self._kde is None:
            raise RuntimeError("Call fit() before scoring")

    @staticmethod
    def _compute_delta_hours(
        timestamps: np.ndarray,
        ref_time: datetime,
    ) -> np.ndarray:
        """Return array of hours elapsed between each timestamp and ref_time."""
        ref_ts = ref_time.timestamp()
        result = np.empty(len(timestamps), dtype=float)
        for i, ts in enumerate(timestamps):
            if isinstance(ts, datetime):
                event_ts = ts.timestamp()
            else:
                event_ts = float(ts)
            delta_s = max(0.0, ref_ts - event_ts)
            result[i] = delta_s / 3600.0
        return result


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def compute_kde_for_bbox(
    conn,
    bbox: tuple[float, float, float, float],
    hours_back: int = 24,
    bandwidth_m: int = 500,
    decay_halflife_hours: float = 24 * 7,
    grid_resolution: float = 0.02,
) -> dict:
    """Query incidents from PostGIS, compute KDE, and return a GeoJSON-compatible dict.

    Parameters
    ----------
    conn:
        Active ``psycopg2`` connection.
    bbox:
        ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326.
    hours_back:
        How many hours of history to include.
    bandwidth_m:
        Spatial bandwidth in metres passed to :class:`TemporalKDE`.
    decay_halflife_hours:
        Temporal decay half-life in hours.
    grid_resolution:
        Grid cell size in degrees.

    Returns
    -------
    dict
        GeoJSON ``FeatureCollection`` where each feature is a grid point with
        a ``density`` property normalised to ``[0, 1]``.
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    sql = """
        SELECT
            ST_X(geom)           AS lon,
            ST_Y(geom)           AS lat,
            severidad,
            EXTRACT(EPOCH FROM timestamp) AS ts_epoch
        FROM sigmac.core_incidentes
        WHERE
            geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            AND timestamp >= NOW() - INTERVAL '%s hours'
            AND severidad IS NOT NULL
        ORDER BY timestamp DESC
    """

    with conn.cursor() as cur:
        cur.execute(sql, (min_lon, min_lat, max_lon, max_lat, hours_back))
        rows = cur.fetchall()

    if not rows:
        logger.warning("compute_kde_for_bbox: no incidents found in bbox", bbox=bbox)
        return {"type": "FeatureCollection", "features": []}

    lons = np.array([r[0] for r in rows], dtype=float)
    lats = np.array([r[1] for r in rows], dtype=float)
    severities = np.array([r[2] for r in rows], dtype=float)
    ts_epochs = np.array([r[3] for r in rows], dtype=float)

    points = np.column_stack([lons, lats])
    now = datetime.now(timezone.utc)

    kde = TemporalKDE(
        bandwidth_meters=bandwidth_m,
        decay_halflife_hours=decay_halflife_hours,
    )
    kde.fit(points, ts_epochs, severities, reference_time=now)

    # Build evaluation grid
    grid_lons = np.arange(min_lon, max_lon, grid_resolution)
    grid_lats = np.arange(min_lat, max_lat, grid_resolution)
    density_2d = kde.score_grid(grid_lons, grid_lats)

    # Normalise to [0, 1]
    d_min, d_max = density_2d.min(), density_2d.max()
    if d_max > d_min:
        density_norm = (density_2d - d_min) / (d_max - d_min)
    else:
        density_norm = np.zeros_like(density_2d)

    # Encode as GeoJSON FeatureCollection
    features: list[dict] = []
    for lat_idx, lat_val in enumerate(grid_lats):
        for lon_idx, lon_val in enumerate(grid_lons):
            d = float(density_norm[lat_idx, lon_idx])
            if d == 0.0:
                continue  # omit zero-density cells to keep payload small
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(float(lon_val), 6), round(float(lat_val), 6)],
                    },
                    "properties": {"density": round(d, 6)},
                }
            )

    logger.info(
        "compute_kde_for_bbox complete",
        n_incidents=len(rows),
        n_grid_points=len(features),
        bbox=bbox,
    )

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "bbox": list(bbox),
            "hours_back": hours_back,
            "bandwidth_m": bandwidth_m,
            "grid_resolution": grid_resolution,
            "n_incidents": len(rows),
            "computed_at": now.isoformat(),
        },
    }
