"""
topography.py
-------------
Topographic adjustment factors for Bolivia's complex terrain.

Bolivia's geography ranges from flat Amazonian lowlands (Llanos) to steep
Andean slopes above 4 000 m.  A spatial bandwidth that is appropriate for
flat terrain over-smooths events in mountainous areas where physical barriers
constrain incident spread.  This module encodes per-zone slope factors that
shrink the effective KDE bandwidth in rugged terrain.

Coordinate convention
---------------------
* All coordinates: WGS 84 / EPSG:4326 (lon, lat degrees)
* Distances returned in metres using the haversine formula
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Earth mean radius in metres
_EARTH_RADIUS_M: float = 6_371_000.0


class TopographyAdjuster:
    """Zone-based topographic adjustment for spatial bandwidth and distance.

    Each zone is defined by a bounding box and a *slope_factor*:

    * ``slope_factor = 1.0``  — flat terrain (Llanos); no bandwidth reduction.
    * ``slope_factor > 1.0``  — rugged terrain; bandwidth divided by this factor.

    Zones are evaluated in definition order; the first matching zone wins.
    Points that fall outside all zones receive ``slope_factor = 1.0``.
    """

    ZONES: dict[str, dict] = {
        # La Paz city centre — very steep quebradas, dense urban fabric
        "la_paz_centro": {
            "lat": (-16.55, -16.45),
            "lon": (-68.20, -68.08),
            "slope_factor": 2.8,
        },
        # El Alto plateau — high but relatively flat w.r.t. La Paz slopes
        "el_alto": {
            "lat": (-16.55, -16.45),
            "lon": (-68.25, -68.15),
            "slope_factor": 1.4,
        },
        # Yungas — extremely rugged cloud-forest descent from the Andes
        "yungas": {
            "lat": (-16.8, -15.5),
            "lon": (-68.0, -67.0),
            "slope_factor": 3.5,
        },
        # Altiplano — high plateau, mostly flat
        "altiplano": {
            "lat": (-22.0, -15.0),
            "lon": (-70.0, -67.0),
            "slope_factor": 1.1,
        },
        # Inter-Andean valleys (Cochabamba, Sucre, Tarija corridors)
        "valles": {
            "lat": (-20.0, -16.0),
            "lon": (-66.5, -63.5),
            "slope_factor": 1.8,
        },
        # Eastern lowlands — flat Amazon/Chaco basin
        "llanos": {
            "lat": (-20.0, -10.0),
            "lon": (-65.0, -57.5),
            "slope_factor": 1.0,
        },
    }

    def get_slope_factor(self, lon: float, lat: float) -> float:
        """Return the slope factor for a given WGS 84 coordinate.

        Parameters
        ----------
        lon:
            Longitude in decimal degrees.
        lat:
            Latitude in decimal degrees.

        Returns
        -------
        float
            Slope factor >= 1.0.  Returns 1.0 for points outside all defined
            zones (treated as flat terrain).
        """
        for zone_name, zone in self.ZONES.items():
            lat_min, lat_max = zone["lat"]
            lon_min, lon_max = zone["lon"]
            if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                logger.debug(
                    "Slope factor resolved",
                    zone=zone_name,
                    lon=lon,
                    lat=lat,
                    factor=zone["slope_factor"],
                )
                return float(zone["slope_factor"])

        logger.debug(
            "Coordinate outside all zones — defaulting slope_factor=1.0",
            lon=lon,
            lat=lat,
        )
        return 1.0

    def adjust_bandwidth(
        self, base_bandwidth_meters: float, lon: float, lat: float
    ) -> float:
        """Reduce the KDE bandwidth for rugged terrain.

        In mountainous areas, the physical connectivity between two points is
        lower than the Euclidean distance suggests.  Dividing the bandwidth by
        the slope factor contracts the kernel, preventing over-smoothing across
        topographic barriers.

        Example
        -------
        La Paz centro (slope_factor = 2.8):
            ``adjust_bandwidth(1000, ...) ≈ 357 m``

        Parameters
        ----------
        base_bandwidth_meters:
            Unadjusted bandwidth in metres.
        lon, lat:
            Location at which to evaluate the adjustment.

        Returns
        -------
        float
            Adjusted bandwidth in metres (always > 0).
        """
        factor = self.get_slope_factor(lon, lat)
        adjusted = base_bandwidth_meters / factor
        return max(adjusted, 1.0)  # guard against zero/negative

    @staticmethod
    def haversine_distance(
        lon1: float, lat1: float, lon2: float, lat2: float
    ) -> float:
        """Great-circle distance in metres between two WGS 84 points.

        Parameters
        ----------
        lon1, lat1:
            Origin coordinates in decimal degrees.
        lon2, lat2:
            Destination coordinates in decimal degrees.

        Returns
        -------
        float
            Distance in metres.
        """
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))

    def adjusted_distance(
        self, lon1: float, lat1: float, lon2: float, lat2: float
    ) -> float:
        """Topographically adjusted distance between two points in metres.

        The raw haversine distance is multiplied by the mean slope factor of
        the two endpoints.  This inflates distances across rugged terrain,
        reflecting higher effective travel cost.

        Parameters
        ----------
        lon1, lat1:
            First point in decimal degrees.
        lon2, lat2:
            Second point in decimal degrees.

        Returns
        -------
        float
            Adjusted distance in metres.
        """
        raw_m = self.haversine_distance(lon1, lat1, lon2, lat2)
        f1 = self.get_slope_factor(lon1, lat1)
        f2 = self.get_slope_factor(lon2, lat2)
        mean_factor = (f1 + f2) / 2.0
        return raw_m * mean_factor
