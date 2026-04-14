"""Redis connection management.

Provides a factory for Redis clients and coordinate-based cache-key
generation, used by the enrich and saves endpoints.
"""
from __future__ import annotations

import hashlib
import logging
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    import redis as redis_mod

logger = logging.getLogger(__name__)


def get_redis_client() -> redis_mod.Redis | None:
    """Create a Redis client from the ``REDIS_URL`` environment variable.

    Returns:
        A connected :class:`redis.Redis` instance, or ``None`` if
        ``REDIS_URL`` is not set or the connection fails.
    """
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis

        return redis.from_url(url, decode_responses=True, socket_timeout=5)
    except Exception:
        logger.exception("Failed to connect to Redis")
        return None


@contextmanager
def redis_connection() -> Generator[redis_mod.Redis | None, None, None]:
    """Context manager that yields a Redis client and closes it on exit.

    Yields:
        A :class:`redis.Redis` instance, or ``None`` if unavailable.

    Example::

        with redis_connection() as r:
            if r:
                r.set("key", "value")
    """
    client = get_redis_client()
    try:
        yield client
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("Error closing Redis connection", exc_info=True)


def cache_key_for_coords(lat: float, lng: float) -> str:
    """Generate a deterministic cache key for a coordinate pair.

    Coordinates are rounded to 4 decimal places (~11 m precision) before
    hashing so that nearby lookups share the same cache entry.

    Args:
        lat: Latitude.
        lng: Longitude.

    Returns:
        A string of the form ``footprint:<md5hex>``.
    """
    raw = f"{lat:.4f},{lng:.4f}"
    return f"footprint:{hashlib.md5(raw.encode()).hexdigest()}"
