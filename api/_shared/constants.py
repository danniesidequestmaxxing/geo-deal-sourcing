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
# Revenue estimation
# ---------------------------------------------------------------------------
REVENUE_PER_SQFT: int = 150

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

# ---------------------------------------------------------------------------
# Rate-limiting delays (seconds)
# ---------------------------------------------------------------------------
PLACES_DELAY: float = 0.05
DETAILS_DELAY: float = 0.05
