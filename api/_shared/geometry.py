"""Geospatial utilities for building-footprint estimation.

Provides the Shoelace polygon-area formula and an Overpass API client with
multi-server failover for querying OpenStreetMap building polygons.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import overpy

from api._shared.constants import (
    MAX_OVERPASS_RETRIES,
    OVERPASS_RADIUS_M,
    OVERPASS_RETRY_BACKOFF,
    OVERPASS_SERVERS,
    SQ_M_TO_SQ_FT,
    TIER_MEDIUM_MAX,
    TIER_SMALL_MAX,
)

logger = logging.getLogger(__name__)


def polygon_area_sq_m(coords: list[tuple[float, float]]) -> float:
    """Calculate polygon area in square metres using the Shoelace formula.

    Converts lat/lng coordinates to a local metre-based coordinate system
    anchored at the first vertex, then applies the standard Shoelace
    (Gauss's area) formula.

    Args:
        coords: List of (latitude, longitude) pairs forming a closed polygon.

    Returns:
        Area in square metres.  Returns 0.0 for degenerate polygons with
        fewer than 3 vertices.
    """
    n = len(coords)
    if n < 3:
        return 0.0

    ref_lat, ref_lng = coords[0]
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(ref_lat))

    pts = [
        ((lat - ref_lat) * m_per_deg_lat, (lng - ref_lng) * m_per_deg_lng)
        for lat, lng in coords
    ]

    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]

    return abs(area) / 2.0


def _largest_building_area(ways: Any) -> float:
    """Find the largest building polygon area from Overpass way results.

    Args:
        ways: Iterable of ``overpy.Way`` objects returned by an Overpass query.

    Returns:
        Area of the largest building in square metres, or 0.0 if no valid
        polygons were found.
    """
    best_area = 0.0
    for way in ways:
        nodes = way.get_nodes(resolve_missing=False)
        coords = [
            (float(n.lat), float(n.lon))
            for n in nodes
            if n.lat is not None
        ]
        if len(coords) < 3:
            continue
        area = polygon_area_sq_m(coords)
        if area > best_area:
            best_area = area
    return best_area


def estimate_building_sqft(lat: float, lng: float) -> float | None:
    """Estimate the largest nearby building footprint in square feet.

    Queries the Overpass API for ``building`` ways within
    :data:`~api._shared.constants.OVERPASS_RADIUS_M` metres of the given
    coordinate.  Automatically retries across multiple Overpass mirror
    servers on failure.

    Args:
        lat: Latitude of the target location.
        lng: Longitude of the target location.

    Returns:
        Estimated building area in square feet, or ``None`` if no buildings
        were found or all queries failed.
    """
    query = f"""
    [out:json][timeout:10];
    (
      way["building"](around:{OVERPASS_RADIUS_M},{lat},{lng});
    );
    out body;
    >;
    out skel qt;
    """

    for attempt in range(MAX_OVERPASS_RETRIES):
        server = OVERPASS_SERVERS[attempt % len(OVERPASS_SERVERS)]
        api = overpy.Overpass(url=server)
        try:
            result = api.query(query)
            if not result.ways:
                return None
            best_area = _largest_building_area(result.ways)
            return best_area * SQ_M_TO_SQ_FT if best_area > 0 else None
        except Exception as exc:
            logger.warning(
                "Overpass query failed (attempt %d/%d, server=%s): %s",
                attempt + 1,
                MAX_OVERPASS_RETRIES,
                server,
                exc,
            )
            if attempt < MAX_OVERPASS_RETRIES - 1:
                time.sleep(OVERPASS_RETRY_BACKOFF[attempt])

    return None


def classify_size_tier(sqft: float | None) -> str:
    """Classify a building by square-footage into a size tier.

    Args:
        sqft: Building area in square feet, or ``None`` if unknown.

    Returns:
        One of ``"Small"``, ``"Medium"``, ``"Large"``, or ``"Unknown"``.
    """
    if sqft is None:
        return "Unknown"
    if sqft < TIER_SMALL_MAX:
        return "Small"
    if sqft <= TIER_MEDIUM_MAX:
        return "Medium"
    return "Large"
