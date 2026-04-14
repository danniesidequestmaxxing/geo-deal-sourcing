"""POST /api/enrich — Building-footprint enrichment endpoint.

Batch-queries building footprints via a multi-source fallback chain
(Overpass 80 m -> Overpass 200 m -> Google viewport estimate) with optional
Redis caching.  Returns estimated square footage, its source, size tier,
and revenue proxy for each place.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._shared.constants import (
    BATCH_CAP,
    CACHE_TTL,
    INTER_QUERY_DELAY,
    REVENUE_PER_SQFT,
)
from api._shared.geometry import (
    classify_size_tier,
    estimate_building_sqft_with_fallback,
)
from api._shared.redis_client import cache_key_for_coords, redis_connection

logger = logging.getLogger(__name__)

app = FastAPI()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class PlaceCoord(BaseModel):
    """A single place to enrich.

    Attributes:
        lat: Latitude of the place.
        lng: Longitude of the place.
        name: Business name (passed through to the response).
        viewport: Optional Google Places geometry.viewport dict with
            ``northeast`` and ``southwest`` sub-dicts.
        business_type: Category string from Google Places (used for context).
        address: Full address string (used for context).
    """

    lat: float
    lng: float
    name: str
    viewport: dict[str, Any] | None = None
    business_type: str = ""
    address: str = ""


class EnrichRequest(BaseModel):
    """Batch enrichment request.

    Attributes:
        places: List of places to look up (capped at
            :data:`~api._shared.constants.BATCH_CAP`).
    """

    places: list[PlaceCoord]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _read_cache(
    r: Any,
    lat: float,
    lng: float,
) -> tuple[bool, float | None, str]:
    """Attempt to read a cached footprint value from Redis.

    Handles both the legacy format (bare ``float | None``) and the new
    format (``{"sqft": ..., "source": ...}``).

    Args:
        r: Redis client (may be ``None``).
        lat: Latitude.
        lng: Longitude.

    Returns:
        A ``(hit, sqft, source)`` tuple.  *hit* is ``True`` if a value was
        found in the cache (even if *sqft* is ``None``).
    """
    if r is None:
        return False, None, "none"
    try:
        key = cache_key_for_coords(lat, lng)
        cached = r.get(key)
        if cached is not None:
            data = json.loads(cached)
            if isinstance(data, dict):
                return True, data.get("sqft"), data.get("source", "osm")
            # Legacy format: bare number or null
            return True, data, "osm" if data is not None else "none"
    except Exception:
        logger.debug("Redis cache read error", exc_info=True)
    return False, None, "none"


def _write_cache(
    r: Any,
    lat: float,
    lng: float,
    sqft: float | None,
    source: str,
) -> None:
    """Store a footprint result in Redis (including ``None`` values).

    Args:
        r: Redis client (may be ``None``).
        lat: Latitude.
        lng: Longitude.
        sqft: Square-footage value to cache.
        source: How the value was derived (e.g. ``"osm"``, ``"viewport"``).
    """
    if r is None:
        return
    try:
        key = cache_key_for_coords(lat, lng)
        payload = json.dumps({"sqft": sqft, "source": source})
        r.set(key, payload, ex=CACHE_TTL)
    except Exception:
        logger.debug("Redis cache write error", exc_info=True)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/enrich")
async def enrich(body: EnrichRequest) -> JSONResponse:
    """Enrich a batch of places with building-footprint data.

    For each place, the endpoint:

    1. Checks Redis for a cached footprint.
    2. Runs the fallback chain (Overpass 80 m -> 200 m -> viewport estimate).
    3. Caches the result for future lookups.
    4. Returns the square footage, its source, size tier, and estimated revenue.

    Args:
        body: The enrichment request containing a list of coordinates.

    Returns:
        JSON with a ``results`` list of enrichment data per place.
    """
    batch = body.places[:BATCH_CAP]
    results: list[dict[str, Any]] = []

    with redis_connection() as r:
        for place in batch:
            cache_hit, sqft, source = _read_cache(r, place.lat, place.lng)

            # Re-run the fallback chain for stale "none" cache entries
            # (from before the category-based fallback was added).
            if cache_hit and source == "none" and sqft is None:
                cache_hit = False

            if not cache_hit:
                sqft, source = estimate_building_sqft_with_fallback(
                    place.lat,
                    place.lng,
                    viewport=place.viewport,
                    business_type=place.business_type,
                )
                _write_cache(r, place.lat, place.lng, sqft, source)
                time.sleep(INTER_QUERY_DELAY)

            size_tier = classify_size_tier(sqft)
            est_revenue = round(sqft * REVENUE_PER_SQFT) if sqft else None

            results.append({
                "name": place.name,
                "sqft": round(sqft) if sqft else None,
                "sqft_source": source,
                "size_tier": size_tier,
                "est_revenue": est_revenue,
            })

    return JSONResponse(content={"results": results})
