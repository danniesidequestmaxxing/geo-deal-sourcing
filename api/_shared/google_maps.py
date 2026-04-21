"""Google Maps API client helpers.

Provides a factory for the Google Maps client and common geocoding
operations used by the search endpoint and the CLI tool.
"""
from __future__ import annotations

import logging
import os

import googlemaps
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def get_gmaps_client(*, api_key: str | None = None) -> googlemaps.Client:
    """Create a Google Maps client.

    Resolves the API key from the *api_key* argument first, then falls back
    to the ``GOOGLE_MAPS_API_KEY`` environment variable.

    Args:
        api_key: Explicit API key.  When ``None``, the environment variable
            is used.

    Returns:
        An authenticated :class:`googlemaps.Client`.

    Raises:
        HTTPException: If no API key is available (HTTP 500).
    """
    key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_MAPS_API_KEY not configured on the server.",
        )
    return googlemaps.Client(key=key)


def geocode_postcode(
    gmaps: googlemaps.Client,
    postcode: str,
) -> tuple[float, float] | None:
    """Geocode an Indonesian postcode to a (lat, lng) pair.

    Args:
        gmaps: An authenticated Google Maps client.
        postcode: A 5-digit Indonesian postcode string.

    Returns:
        A ``(latitude, longitude)`` tuple, or ``None`` if geocoding failed.
    """
    try:
        results = gmaps.geocode(f"{postcode}, Indonesia")
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as exc:
        logger.warning("Geocode failed for postcode %s: %s", postcode, exc)
    return None
