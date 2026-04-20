"""Shared constants used across API endpoints and the CLI tool.

Centralises magic numbers and configuration values so they can be maintained
in one place instead of being duplicated across modules.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Unit conversions
# ---------------------------------------------------------------------------
SQ_M_TO_SQ_FT: float = 10.7639

# ---------------------------------------------------------------------------
# Overpass API (OpenStreetMap building footprints)
# ---------------------------------------------------------------------------
OVERPASS_RADIUS_M: int = 80
OVERPASS_RADIUS_WIDE: int = 200

OVERPASS_SERVERS: list[str] = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

MAX_OVERPASS_RETRIES: int = 3
OVERPASS_RETRY_BACKOFF: list[float] = [1.0, 2.0, 4.0]
INTER_QUERY_DELAY: float = 0.5

# ---------------------------------------------------------------------------
# Building-size tier thresholds (square feet)
# ---------------------------------------------------------------------------
TIER_SMALL_MAX: int = 20_000
TIER_MEDIUM_MAX: int = 100_000

# ---------------------------------------------------------------------------
# Category-based default square-footage estimates
# ---------------------------------------------------------------------------
# When all geometric methods fail (Overpass + viewport), we fall back to a
# reasonable estimate based on the Google Places business category.  These
# numbers represent typical Malaysian industrial/commercial building sizes.
CATEGORY_SQFT_DEFAULTS: dict[str, int] = {
    "factory": 45_000,
    "manufacturing": 45_000,
    "warehouse": 35_000,
    "industrial": 40_000,
    "logistics": 30_000,
    "storage": 25_000,
    "engineering": 20_000,
    "trading": 15_000,
    "enterprise": 15_000,
    "office": 10_000,
    "wholesale": 20_000,
    "construction": 25_000,
    "food": 12_000,
    "chemical": 30_000,
    "textile": 25_000,
    "metal": 30_000,
    "plastic": 25_000,
    "electronics": 20_000,
    "furniture": 20_000,
    "automotive": 25_000,
    "pharmaceutical": 25_000,
    "printing": 15_000,
}
CATEGORY_SQFT_FALLBACK: int = 15_000  # generic default when no category matches

# ---------------------------------------------------------------------------
# Revenue estimation
# ---------------------------------------------------------------------------
REVENUE_PER_SQFT: int = 150

# ---------------------------------------------------------------------------
# Viewport-based estimation
# ---------------------------------------------------------------------------
VIEWPORT_BUILDING_RATIO: float = 0.35
MAX_VIEWPORT_SQFT: int = 2_000_000

# ---------------------------------------------------------------------------
# Redis cache
# ---------------------------------------------------------------------------
CACHE_TTL: int = 60 * 60 * 24 * 7  # 7 days — buildings don't change often

# ---------------------------------------------------------------------------
# Web scraping / HTTP
# ---------------------------------------------------------------------------
WEB_SCRAPE_TIMEOUT: int = 6
MAX_TEXT_PER_SITE: int = 1500
BATCH_CAP: int = 10

USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Rate-limiting delays (seconds)
# ---------------------------------------------------------------------------
PLACES_DELAY: float = 0.05
DETAILS_DELAY: float = 0.05
