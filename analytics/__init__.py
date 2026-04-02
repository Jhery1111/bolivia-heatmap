"""
analytics — SIGMAC Bolivia incident analysis package.

Public surface
--------------
HeatmapEngine
    Orchestrates KDE, clustering, and topographic adjustment to produce
    heatmap GeoJSON, anomaly lists, and real-time summaries.

TemporalKDE
    Weighted Gaussian KDE with exponential temporal decay and severity
    scaling.  Operates in geographic degrees; bandwidth supplied in metres.

IncidentClusterer
    DBSCAN-based spatial clustering using the haversine metric.  Identifies
    anomalous clusters whose density significantly exceeds a historical
    baseline.

TopographyAdjuster
    Zone-based topographic adjustment for Bolivia's complex terrain.
    Reduces KDE bandwidth in steep areas (La Paz, Yungas) and inflates
    effective distances to reflect higher travel cost.
"""

from .heatmap_engine import HeatmapEngine
from .kde_temporal import TemporalKDE
from .clustering import IncidentClusterer
from .topography import TopographyAdjuster

__all__ = [
    "HeatmapEngine",
    "TemporalKDE",
    "IncidentClusterer",
    "TopographyAdjuster",
]
