"""Geospatial utilities for building-footprint estimation.

Provides the Shoelace polygon-area formula, an Overpass API client with
multi-server failover, a Google Places viewport-based estimator, and a
unified fallback chain that always tries to produce a square-footage figure.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import overpy

from api._shared.constants import (
    MAX_OVERPASS_RETRIES,
    MAX_VIEWPORT_SQFT,
    OVERPASS_RADIUS_M,
    OVERPASS_RADIUS_WIDE,
    OVERPASS_RETRY_BACKOFF,
    OVERPASS_SERVERS,
    SQ_M_TO_SQ_FT,
    TIER_MEDIUM_MAX,
    TIER_SMALL_MAX,
    VIEWPORT_BUILDING_RATIO,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core geometry
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Overpass helpers
# ---------------------------------------------------------------------------
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


def _overpass_building_query(
    lat: float,
    lng: float,
    radius: int,
    *,
    max_retries: int = MAX_OVERPASS_RETRIES,
) -> float | None:
    """Query Overpass for the largest building footprint at a given radius.

    Args:
        lat: Latitude of the target location.
        lng: Longitude of the target location.
        radius: Search radius in metres.
        max_retries: Number of retry attempts across mirror servers.

    Returns:
        Building area in square feet, or ``None`` if no buildings found.
    """
    query = f"""
    [out:json][timeout:10];
    (
      way["building"](around:{radius},{lat},{lng});
    );
    out body;
    >;
    out skel qt;
    """

    for attempt in range(max_retries):
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
                "Overpass query failed (attempt %d/%d, radius=%dm, server=%s): %s",
                attempt + 1,
                max_retries,
                radius,
                server,
                exc,
            )
            if attempt < max_retries - 1:
                time.sleep(OVERPASS_RETRY_BACKOFF[attempt])

    return None


def estimate_building_sqft(lat: float, lng: float) -> float | None:
    """Estimate building footprint at the default 80 m radius.

    Convenience wrapper around :func:`_overpass_building_query` with the
    standard radius and full retry chain.

    Args:
        lat: Latitude of the target location.
        lng: Longitude of the target location.

    Returns:
        Estimated building area in square feet, or ``None``.
    """
    return _overpass_building_query(lat, lng, OVERPASS_RADIUS_M)


# ---------------------------------------------------------------------------
# Viewport-based estimation
# ---------------------------------------------------------------------------
def estimate_sqft_from_viewport(viewport: dict[str, Any] | None) -> float | None:
    """Estimate building area from Google Places viewport bounds.

    The viewport is the recommended map view for a place.  For industrial
    facilities it roughly corresponds to the property boundary.  We calculate
    the viewport area and apply a scaling factor
    (:data:`~api._shared.constants.VIEWPORT_BUILDING_RATIO`) to approximate
    the building footprint.

    Args:
        viewport: Dict with ``northeast`` and ``southwest`` sub-dicts, each
            containing ``lat`` and ``lng`` keys.  ``None`` is safe to pass.

    Returns:
        Estimated square feet, or ``None`` if the viewport is missing or
        unreasonably large.
    """
    if not viewport:
        return None

    ne = viewport.get("northeast", {})
    sw = viewport.get("southwest", {})
    if (
        ne.get("lat") is None or ne.get("lng") is None
        or sw.get("lat") is None or sw.get("lng") is None
    ):
        return None

    coords = [
        (ne["lat"], ne["lng"]),
        (ne["lat"], sw["lng"]),
        (sw["lat"], sw["lng"]),
        (sw["lat"], ne["lng"]),
    ]
    area_sqft = polygon_area_sq_m(coords) * SQ_M_TO_SQ_FT

    if area_sqft > MAX_VIEWPORT_SQFT:
        return None

    return area_sqft * VIEWPORT_BUILDING_RATIO


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------
def estimate_building_sqft_with_fallback(
    lat: float,
    lng: float,
    viewport: dict[str, Any] | None = None,
) -> tuple[float | None, str]:
    """Estimate building footprint using a multi-source fallback chain.

    The chain tries, in order:

    1. **Overpass 80 m** — highest accuracy, full retry across mirror servers.
    2. **Overpass 200 m** — wider search (single attempt) for slight
       coordinate misalignment.
    3. **Google viewport estimate** — always available when the search
       response included geometry data.

    Args:
        lat: Latitude of the target location.
        lng: Longitude of the target location.
        viewport: Optional Google Places ``geometry.viewport`` dict.

    Returns:
        A ``(sqft, source)`` tuple where *source* is one of:

        - ``"osm"`` — Overpass at 80 m radius
        - ``"osm_wide"`` — Overpass at 200 m radius
        - ``"viewport"`` — Google Places viewport estimate
        - ``"none"`` — all sources failed
    """
    # 1. Overpass at default radius (80 m) — full retry
    sqft = _overpass_building_query(lat, lng, OVERPASS_RADIUS_M)
    if sqft is not None:
        return sqft, "osm"

    # 2. Overpass at wider radius (200 m) — single attempt
    sqft = _overpass_building_query(
        lat, lng, OVERPASS_RADIUS_WIDE, max_retries=1,
    )
    if sqft is not None:
        return sqft, "osm_wide"

    # 3. Google Places viewport estimate
    sqft = estimate_sqft_from_viewport(viewport)
    if sqft is not None:
        return sqft, "viewport"

    return None, "none"


# ---------------------------------------------------------------------------
# Size tier classification
# ---------------------------------------------------------------------------
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
