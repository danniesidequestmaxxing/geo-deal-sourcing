"""
POST /api/search
Two modes:
  1. Postcode mode — geocode a Malaysian postcode, search Google Places for
     businesses, and enrich each result with Place Details.
  2. Company mode — search for a specific company name in Malaysia and return
     its enriched details.
"""
from __future__ import annotations
import os
import re
import time
from typing import Any
import googlemaps
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

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
PLACES_DELAY = 0.05
DETAILS_DELAY = 0.05


class SearchRequest(BaseModel):
    postcode: str = ""
    mode: str = "postcode"       # "postcode" | "company"
    company: str = ""


def _get_gmaps() -> googlemaps.Client:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY not configured on the server.")
    return googlemaps.Client(key=api_key)


def _enrich_place(gmaps: googlemaps.Client, place: dict[str, Any], fallback_lat: float, fallback_lng: float) -> dict[str, Any] | None:
    """Fetch Place Details and return a normalised dict."""
    detail: dict[str, Any] = {}
    try:
        detail_resp = gmaps.place(
            place_id=place["place_id"],
            fields=[
                "name", "formatted_address", "formatted_phone_number",
                "international_phone_number", "website", "geometry", "type",
            ],
        )
        detail = detail_resp.get("result", {})
    except Exception:
        pass

    geom = detail.get("geometry", place.get("geometry", {}))
    types_list = detail.get("types", detail.get("type", place.get("types", [])))
    types_str = ", ".join(
        t.replace("_", " ").title()
        for t in types_list
        if t not in ("establishment", "point_of_interest")
    )
    phone = detail.get("formatted_phone_number", "") or detail.get("international_phone_number", "")
    return {
        "name": detail.get("name", place.get("name", "")),
        "category": types_str,
        "address": detail.get("formatted_address", place.get("formatted_address", "")),
        "phone": phone,
        "website": detail.get("website", ""),
        "lat": geom.get("location", {}).get("lat", fallback_lat),
        "lng": geom.get("location", {}).get("lng", fallback_lng),
    }


JUNK_NAMES = {"sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.", "malaysia", "(malaysia)", ""}


def _filter_junk(enriched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for e in enriched:
        name = e["name"].strip()
        if len(name) < 3:
            continue
        core = name.lower().strip("() .")
        for suffix in ["sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.", "(m)", "(malaysia)", "malaysia"]:
            core = core.replace(suffix, "").strip(" .,()-")
        if not core or core in JUNK_NAMES:
            continue
        filtered.append(e)
    return filtered


# ---- Postcode-based search ------------------------------------------------

def _search_by_postcode(gmaps: googlemaps.Client, postcode: str) -> JSONResponse:
    if not re.fullmatch(r"\d{5}", postcode):
        raise HTTPException(status_code=400, detail="Invalid postcode. Must be exactly 5 digits.")

    try:
        geo_results = gmaps.geocode(f"{postcode}, Malaysia")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geocoding failed: {exc}")
    if not geo_results:
        raise HTTPException(status_code=404, detail=f"Could not geocode postcode {postcode}.")

    loc = geo_results[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]

    seen_ids: set[str] = set()
    raw_places: list[dict[str, Any]] = []
    debug_log: list[str] = []

    for keyword in SEARCH_KEYWORDS:
        query = f"{keyword} {postcode} Malaysia"
        try:
            resp = gmaps.places(query=query, location=(lat, lng), radius=5000)
            debug_log.append(f"{keyword}: status={resp.get('status','?')}, results={len(resp.get('results', []))}")
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
        return JSONResponse(content={"places": [], "count": 0, "postcode": postcode,
                                      "centroid": {"lat": lat, "lng": lng}, "debug": debug_log})

    enriched: list[dict[str, Any]] = []
    for place in raw_places:
        row = _enrich_place(gmaps, place, lat, lng)
        if row:
            enriched.append(row)
        time.sleep(DETAILS_DELAY)

    enriched = _filter_junk(enriched)

    return JSONResponse(content={
        "places": enriched,
        "count": len(enriched),
        "postcode": postcode,
        "centroid": {"lat": lat, "lng": lng},
        "debug": debug_log,
    })


# ---- Company name search --------------------------------------------------

def _search_by_company(gmaps: googlemaps.Client, company: str) -> JSONResponse:
    company = company.strip()
    if len(company) < 2:
        raise HTTPException(status_code=400, detail="Company name too short.")

    query = f"{company} Malaysia"
    debug_log: list[str] = []

    try:
        resp = gmaps.places(query=query)
        debug_log.append(f"query='{query}': status={resp.get('status','?')}, results={len(resp.get('results', []))}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Places search failed: {exc}")

    raw_places = resp.get("results", [])
    if not raw_places:
        return JSONResponse(content={"places": [], "count": 0, "company": company, "debug": debug_log})

    enriched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
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


# ---- Main endpoint --------------------------------------------------------

@app.post("/api/search")
async def search(body: SearchRequest):
    gmaps = _get_gmaps()

    if body.mode == "company":
        return _search_by_company(gmaps, body.company)
    else:
        return _search_by_postcode(gmaps, body.postcode)
