"""POST /api/enrich — Building-footprint enrichment endpoint.

Batch-queries building footprints via multiple Overpass servers with retry
logic and optional Redis caching.  Returns estimated square footage, size
tier, and revenue proxy for each place.
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
from api._shared.geometry import classify_size_tier, estimate_building_sqft
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
    """

    lat: float
    lng: float
    name: str


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
) -> tuple[bool, float | None]:
    """Attempt to read a cached footprint value from Redis.

    Args:
        r: Redis client (may be ``None``).
        lat: Latitude.
        lng: Longitude.

    Returns:
        A ``(hit, sqft)`` tuple.  *hit* is ``True`` if the value was found
        in the cache (even if *sqft* is ``None``).
    """
    if r is None:
        return False, None
    try:
        key = cache_key_for_coords(lat, lng)
        cached = r.get(key)
        if cached is not None:
            return True, json.loads(cached)
    except Exception:
        logger.debug("Redis cache read error", exc_info=True)
    return False, None


def _write_cache(r: Any, lat: float, lng: float, sqft: float | None) -> None:
    """Store a footprint result in Redis (including ``None`` values).

    Args:
        r: Redis client (may be ``None``).
        lat: Latitude.
        lng: Longitude.
        sqft: Square-footage value to cache.
    """
    if r is None:
        return
    try:
        key = cache_key_for_coords(lat, lng)
        r.set(key, json.dumps(sqft), ex=CACHE_TTL)
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
    2. Falls back to an Overpass API query if not cached.
    3. Caches the result for future lookups.
    4. Returns the square footage, size tier, and estimated revenue.

    Args:
        body: The enrichment request containing a list of coordinates.

    Returns:
        JSON with a ``results`` list of enrichment data per place.
    """
    batch = body.places[:BATCH_CAP]
    results: list[dict[str, Any]] = []

    with redis_connection() as r:
        for place in batch:
            cache_hit, sqft = _read_cache(r, place.lat, place.lng)

            if not cache_hit:
                sqft = estimate_building_sqft(place.lat, place.lng)
                _write_cache(r, place.lat, place.lng, sqft)
                time.sleep(INTER_QUERY_DELAY)

            size_tier = classify_size_tier(sqft)
            est_revenue = round(sqft * REVENUE_PER_SQFT) if sqft else None

            results.append({
                "name": place.name,
                "sqft": round(sqft) if sqft else None,
                "size_tier": size_tier,
                "est_revenue": est_revenue,
            })

    return JSONResponse(content={"results": results})
