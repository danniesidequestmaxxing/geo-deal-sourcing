"""
POST /api/enrich
Batch-query building footprints via multiple Overpass servers with retry logic
and Redis caching.  Returns estimated square footage, size tier, and revenue
proxy for each place.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Any

import overpy
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SQ_M_TO_SQ_FT = 10.7639
OVERPASS_RADIUS_M = 80
REVENUE_PER_SQFT = 150
TIER_SMALL_MAX = 20_000
TIER_MEDIUM_MAX = 100_000
BATCH_CAP = 10

# Multiple public Overpass endpoints for failover
OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 2.0, 4.0]   # seconds between retries
INTER_QUERY_DELAY = 0.5            # seconds between per-place queries

# Redis cache TTL for footprints (7 days — buildings don't change often)
CACHE_TTL = 60 * 60 * 24 * 7


# ---------------------------------------------------------------------------
# Redis helper
# ---------------------------------------------------------------------------
def _get_redis():
    """Return a Redis client or None if not configured."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis
        return redis.from_url(url, decode_responses=True, socket_timeout=5)
    except Exception:
        return None


def _cache_key(lat: float, lng: float) -> str:
    """Deterministic cache key for a coordinate pair (rounded to ~11 m)."""
    raw = f"{lat:.4f},{lng:.4f}"
    return f"footprint:{hashlib.md5(raw.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
class PlaceCoord(BaseModel):
    lat: float
    lng: float
    name: str


class EnrichRequest(BaseModel):
    places: list[PlaceCoord]


def _polygon_area_sq_m(coords: list[tuple[float, float]]) -> float:
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
# Overpass query with multi-server retry
# ---------------------------------------------------------------------------
def estimate_building_sqft(lat: float, lng: float) -> float | None:
    """Query building footprint from Overpass with failover across servers."""
    query = f"""
    [out:json][timeout:10];
    (
      way["building"](around:{OVERPASS_RADIUS_M},{lat},{lng});
    );
    out body;
    >;
    out skel qt;
    """

    last_error = None
    for attempt in range(MAX_RETRIES):
        server = OVERPASS_SERVERS[attempt % len(OVERPASS_SERVERS)]
        api = overpy.Overpass(url=server)
        try:
            result = api.query(query)
            # Success — extract the largest building polygon
            if not result.ways:
                return None
            best_area = 0.0
            for way in result.ways:
                nodes = way.get_nodes(resolve_missing=False)
                coords = [
                    (float(n.lat), float(n.lon))
                    for n in nodes
                    if n.lat is not None
                ]
                if len(coords) < 3:
                    continue
                area = _polygon_area_sq_m(coords)
                if area > best_area:
                    best_area = area
            return best_area * SQ_M_TO_SQ_FT if best_area > 0 else None

        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])

    return None


def classify_size_tier(sqft: float | None) -> str:
    if sqft is None:
        return "Unknown"
    if sqft < TIER_SMALL_MAX:
        return "Small"
    if sqft <= TIER_MEDIUM_MAX:
        return "Medium"
    return "Large"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/enrich")
async def enrich(body: EnrichRequest):
    batch = body.places[:BATCH_CAP]
    results: list[dict[str, Any]] = []
    r = _get_redis()

    for place in batch:
        sqft: float | None = None
        cache_hit = False

        # 1. Check Redis cache
        if r:
            try:
                key = _cache_key(place.lat, place.lng)
                cached = r.get(key)
                if cached is not None:
                    sqft = json.loads(cached)  # could be a number or null
                    cache_hit = True
            except Exception:
                pass

        # 2. Query Overpass if no cache hit
        if not cache_hit:
            sqft = estimate_building_sqft(place.lat, place.lng)

            # 3. Store result in Redis (cache even None results)
            if r:
                try:
                    key = _cache_key(place.lat, place.lng)
                    r.set(key, json.dumps(sqft), ex=CACHE_TTL)
                except Exception:
                    pass

            time.sleep(INTER_QUERY_DELAY)

        size_tier = classify_size_tier(sqft)
        est_revenue = round(sqft * REVENUE_PER_SQFT) if sqft else None

        results.append({
            "name": place.name,
            "sqft": round(sqft) if sqft else None,
            "size_tier": size_tier,
            "est_revenue": est_revenue,
        })

    if r:
        try:
            r.close()
        except Exception:
            pass

    return JSONResponse(content={"results": results})
