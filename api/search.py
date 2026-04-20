"""POST /api/search — Google Places discovery endpoint.

Supports two modes:

1. **Postcode mode** — geocode a Malaysian postcode, search Google Places for
   businesses within 5 km, and enrich each result with Place Details.
2. **Company mode** — search for a specific company name in Malaysia and return
   its enriched details.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import googlemaps
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._shared.constants import DETAILS_DELAY, PLACES_DELAY
from api._shared.google_maps import get_gmaps_client

logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------------------------------------------------------------------
# Search keywords (broader set for the web UI vs. the CLI tool)
# ---------------------------------------------------------------------------
SEARCH_KEYWORDS: list[str] = [
    "company",
    "sdn bhd",
    "factory",
    "manufacturing",
    "warehouse",
    "industrial",
    "enterprise",
    "trading",
    "logistics",
    "engineering",
]

# Names too generic / meaningless to surface as leads
_JUNK_NAMES: frozenset[str] = frozenset({
    "sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.", "malaysia",
    "(malaysia)", "",
})

_JUNK_SUFFIXES: list[str] = [
    "sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.",
    "(m)", "(malaysia)", "malaysia",
]


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class SearchRequest(BaseModel):
    """Incoming search request body.

    Attributes:
        postcode: A 5-digit Malaysian postcode (postcode mode).
        mode: Either ``"postcode"``, ``"company"``, or ``"polygon"``.
        company: Company name to look up (company mode).
        polygon: List of ``[lat, lng]`` pairs forming a closed polygon
            (polygon mode).  Minimum 3 vertices.
    """

    postcode: str = ""
    mode: str = "postcode"
    company: str = ""
    polygon: list[list[float]] = []


# ---------------------------------------------------------------------------
# Place enrichment
# ---------------------------------------------------------------------------
_DETAIL_FIELDS: list[str] = [
    "name",
    "formatted_address",
    "formatted_phone_number",
    "international_phone_number",
    "website",
    "geometry",
    "type",
    "business_status",
]


def _polygon_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Return the arithmetic centroid of a polygon's vertices."""
    lat = sum(p[0] for p in polygon) / len(polygon)
    lng = sum(p[1] for p in polygon) / len(polygon)
    return lat, lng


def _polygon_radius_m(polygon: list[list[float]], centroid: tuple[float, float]) -> float:
    """Return the max distance (metres) from centroid to any vertex.

    Uses the equirectangular approximation, which is adequate for the
    small distances involved in a Malaysian industrial search area.
    """
    import math
    clat, clng = centroid
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(clat))
    best = 0.0
    for lat, lng in polygon:
        dx = (lng - clng) * m_per_deg_lng
        dy = (lat - clat) * m_per_deg_lat
        d = math.hypot(dx, dy)
        if d > best:
            best = d
    return best


def _point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test.

    Args:
        lat: Point latitude.
        lng: Point longitude.
        polygon: List of ``[lat, lng]`` pairs.

    Returns:
        True if the point is inside the polygon.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i]
        lat_j, lng_j = polygon[j]
        if ((lng_i > lng) != (lng_j > lng)) and (
            lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i + 1e-12) + lat_i
        ):
            inside = not inside
        j = i
    return inside


def _extract_postcode(address: str) -> str:
    """Extract a 5-digit Malaysian postcode from a formatted address.

    Args:
        address: A Google Places formatted address string.

    Returns:
        The extracted postcode, or an empty string if none found.
    """
    match = re.search(r"\b(\d{5})\b", address)
    return match.group(1) if match else ""


def _enrich_place(
    gmaps: googlemaps.Client,
    place: dict[str, Any],
    fallback_lat: float,
    fallback_lng: float,
) -> dict[str, Any] | None:
    """Fetch Place Details and return a normalised dict.

    Args:
        gmaps: Authenticated Google Maps client.
        place: Raw place result from a text-search response.
        fallback_lat: Default latitude when the detail response has none.
        fallback_lng: Default longitude when the detail response has none.

    Returns:
        A flat dict with keys ``name``, ``category``, ``address``, ``phone``,
        ``website``, ``lat``, ``lng``, or ``None`` if enrichment failed.
    """
    detail: dict[str, Any] = {}
    try:
        detail_resp = gmaps.place(
            place_id=place["place_id"],
            fields=_DETAIL_FIELDS,
        )
        detail = detail_resp.get("result", {})
    except googlemaps.exceptions.ApiError as exc:
        logger.warning("Place Details API error for %s: %s", place["place_id"], exc)
    except Exception as exc:
        logger.warning("Unexpected error enriching place %s: %s", place["place_id"], exc)

    geom = detail.get("geometry", place.get("geometry", {}))
    types_list = detail.get("types", detail.get("type", place.get("types", [])))
    types_str = ", ".join(
        t.replace("_", " ").title()
        for t in types_list
        if t not in ("establishment", "point_of_interest")
    )
    phone = (
        detail.get("formatted_phone_number", "")
        or detail.get("international_phone_number", "")
    )

    address = detail.get("formatted_address", place.get("formatted_address", ""))
    return {
        "name": detail.get("name", place.get("name", "")),
        "category": types_str,
        "address": address,
        "phone": phone,
        "website": detail.get("website", ""),
        "lat": geom.get("location", {}).get("lat", fallback_lat),
        "lng": geom.get("location", {}).get("lng", fallback_lng),
        "place_id": place.get("place_id", ""),
        "business_status": detail.get("business_status", ""),
        "viewport": geom.get("viewport", {}),
        "postcode": _extract_postcode(address),
    }


# ---------------------------------------------------------------------------
# Junk filtering
# ---------------------------------------------------------------------------
def _filter_junk(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove results with meaningless or overly-generic business names.

    Args:
        enriched: List of enriched place dicts.

    Returns:
        Filtered list with junk entries removed.
    """
    filtered: list[dict[str, Any]] = []
    for entry in enriched:
        name = entry["name"].strip()
        if len(name) < 3:
            continue
        core = name.lower().strip("() .")
        for suffix in _JUNK_SUFFIXES:
            core = core.replace(suffix, "").strip(" .,()-")
        if not core or core in _JUNK_NAMES:
            continue
        filtered.append(entry)
    return filtered


# ---------------------------------------------------------------------------
# Postcode-based search
# ---------------------------------------------------------------------------
def _search_by_postcode(
    gmaps: googlemaps.Client,
    postcode: str,
) -> JSONResponse:
    """Search for businesses near a Malaysian postcode.

    Args:
        gmaps: Authenticated Google Maps client.
        postcode: A 5-digit Malaysian postcode.

    Returns:
        JSON response with ``places``, ``count``, ``postcode``, ``centroid``,
        and ``debug`` fields.

    Raises:
        HTTPException: On invalid postcode or geocoding failure.
    """
    if not re.fullmatch(r"\d{5}", postcode):
        raise HTTPException(
            status_code=400,
            detail="Invalid postcode. Must be exactly 5 digits.",
        )

    try:
        geo_results = gmaps.geocode(f"{postcode}, Malaysia")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geocoding failed: {exc}")
    if not geo_results:
        raise HTTPException(
            status_code=404,
            detail=f"Could not geocode postcode {postcode}.",
        )

    loc = geo_results[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    seen_ids: set[str] = set()
    raw_places: list[dict[str, Any]] = []
    debug_log: list[str] = []

    for keyword in SEARCH_KEYWORDS:
        query = f"{keyword} {postcode} Malaysia"
        try:
            resp = gmaps.places(query=query, location=(lat, lng), radius=5000)
            debug_log.append(
                f"{keyword}: status={resp.get('status', '?')}, "
                f"results={len(resp.get('results', []))}"
            )
        except Exception as exc:
            debug_log.append(f"{keyword}: EXCEPTION={exc}")
            time.sleep(PLACES_DELAY)
            continue

        for place in resp.get("results", []):
            pid = place["place_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                raw_places.append(place)
        time.sleep(PLACES_DELAY)

    if not raw_places:
        return JSONResponse(content={
            "places": [],
            "count": 0,
            "postcode": postcode,
            "centroid": {"lat": lat, "lng": lng},
            "debug": debug_log,
        })

    enriched = [
        row
        for place in raw_places
        if (row := _enrich_place(gmaps, place, lat, lng)) is not None
    ]
    for _ in enriched:
        time.sleep(DETAILS_DELAY)

    enriched = _filter_junk(enriched)

    # Filter out results whose address postcode doesn't match the search.
    # Google Places text search uses location/radius as a bias, not a hard
    # boundary, so results from distant postcodes frequently appear.
    enriched = [
        e for e in enriched
        if e.get("postcode", "") == postcode
    ]

    return JSONResponse(content={
        "places": enriched,
        "count": len(enriched),
        "postcode": postcode,
        "centroid": {"lat": lat, "lng": lng},
        "debug": debug_log,
    })


# ---------------------------------------------------------------------------
# Polygon-based search
# ---------------------------------------------------------------------------
def _search_by_polygon(
    gmaps: googlemaps.Client,
    polygon: list[list[float]],
) -> JSONResponse:
    """Search for businesses inside a user-drawn polygon.

    Computes the polygon centroid and a radius covering all vertices,
    runs a Nearby Search (hard radius boundary) for each keyword,
    and filters the enriched results using a point-in-polygon test.

    Args:
        gmaps: Authenticated Google Maps client.
        polygon: List of ``[lat, lng]`` pairs (minimum 3 vertices).

    Returns:
        JSON response with ``places``, ``count``, ``polygon``, ``centroid``.

    Raises:
        HTTPException: If the polygon has fewer than 3 vertices.
    """
    if len(polygon) < 3:
        raise HTTPException(
            status_code=400,
            detail="Polygon must have at least 3 vertices.",
        )

    centroid_lat, centroid_lng = _polygon_centroid(polygon)
    radius_m = int(_polygon_radius_m(polygon, (centroid_lat, centroid_lng)))
    # Cap radius to Google Places' 50 km max; also ensure a minimum.
    radius_m = max(500, min(radius_m, 50_000))

    seen_ids: set[str] = set()
    raw_places: list[dict[str, Any]] = []
    debug_log: list[str] = []

    for keyword in SEARCH_KEYWORDS:
        try:
            resp = gmaps.places_nearby(
                location=(centroid_lat, centroid_lng),
                radius=radius_m,
                keyword=keyword,
            )
            debug_log.append(
                f"{keyword}: status={resp.get('status', '?')}, "
                f"results={len(resp.get('results', []))}"
            )
        except Exception as exc:
            debug_log.append(f"{keyword}: EXCEPTION={exc}")
            time.sleep(PLACES_DELAY)
            continue

        for place in resp.get("results", []):
            pid = place["place_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                raw_places.append(place)
        time.sleep(PLACES_DELAY)

    if not raw_places:
        return JSONResponse(content={
            "places": [],
            "count": 0,
            "polygon": polygon,
            "centroid": {"lat": centroid_lat, "lng": centroid_lng},
            "debug": debug_log,
        })

    enriched = [
        row
        for place in raw_places
        if (row := _enrich_place(gmaps, place, centroid_lat, centroid_lng)) is not None
    ]
    for _ in enriched:
        time.sleep(DETAILS_DELAY)

    enriched = _filter_junk(enriched)

    # Filter to points inside the drawn polygon.
    enriched = [
        e for e in enriched
        if _point_in_polygon(e["lat"], e["lng"], polygon)
    ]

    return JSONResponse(content={
        "places": enriched,
        "count": len(enriched),
        "polygon": polygon,
        "centroid": {"lat": centroid_lat, "lng": centroid_lng},
        "debug": debug_log,
    })


# ---------------------------------------------------------------------------
# Company-name search
# ---------------------------------------------------------------------------
def _search_by_company(
    gmaps: googlemaps.Client,
    company: str,
) -> JSONResponse:
    """Look up a specific company name via Google Places.

    Args:
        gmaps: Authenticated Google Maps client.
        company: Company name to search for.

    Returns:
        JSON response with ``places``, ``count``, ``company``, and ``debug``.

    Raises:
        HTTPException: If the company name is too short or the API call fails.
    """
    company = company.strip()
    if len(company) < 2:
        raise HTTPException(status_code=400, detail="Company name too short.")

    query = f"{company} Malaysia"
    debug_log: list[str] = []

    try:
        resp = gmaps.places(query=query)
        debug_log.append(
            f"query='{query}': status={resp.get('status', '?')}, "
            f"results={len(resp.get('results', []))}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Places search failed: {exc}"
        )

    raw_places = resp.get("results", [])
    if not raw_places:
        return JSONResponse(content={
            "places": [],
            "count": 0,
            "company": company,
            "debug": debug_log,
        })

    seen_ids: set[str] = set()
    enriched: list[dict[str, Any]] = []
    for place in raw_places:
        pid = place["place_id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        row = _enrich_place(gmaps, place, 0, 0)
        if row:
            enriched.append(row)
        time.sleep(DETAILS_DELAY)

    return JSONResponse(content={
        "places": enriched,
        "count": len(enriched),
        "company": company,
        "debug": debug_log,
    })


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/search")
async def search(body: SearchRequest) -> JSONResponse:
    """Route the search request to the appropriate handler.

    Args:
        body: Parsed request containing the search mode and parameters.

    Returns:
        A JSON response with enriched place results.
    """
    gmaps = get_gmaps_client()

    if body.mode == "company":
        return _search_by_company(gmaps, body.company)
    if body.mode == "polygon":
        return _search_by_polygon(gmaps, body.polygon)
    return _search_by_postcode(gmaps, body.postcode)
