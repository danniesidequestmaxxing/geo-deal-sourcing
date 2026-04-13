"""
POST /api/search
Geocode a Malaysian postcode, search Google Places for businesses,
and enrich each result with Place Details (phone, website).
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
    postcode: str

@app.post("/api/search")
async def search(body: SearchRequest):
    postcode = body.postcode.strip()
    if not re.fullmatch(r"\d{5}", postcode):
        raise HTTPException(status_code=400, detail="Invalid postcode. Must be exactly 5 digits.")
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY not configured on the server.")
    gmaps = googlemaps.Client(key=api_key)
    try:
        geo_results = gmaps.geocode(f"{postcode}, Malaysia")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geocoding failed: {exc}")
    if not geo_results:
        raise HTTPException(status_code=404, detail=f"Could not geocode postcode {postcode}. Try a different postcode.")
    loc = geo_results[0]["geometry"]["location"]
    lat, lng = loc["lat"], loc["lng"]
    seen_ids: set[str] = set()
    raw_places: list[dict[str, Any]] = []
    debug_log: list[str] = []
    for keyword in SEARCH_KEYWORDS:
        query = f"{keyword} {postcode} Malaysia"
        try:
            resp = gmaps.places(query=query, location=(lat, lng), radius=5000)
            status = resp.get("status", "NO_STATUS")
            count = len(resp.get("results", []))
            debug_log.append(f"{keyword}: status={status}, results={count}")
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
    enriched: list[dict[str, Any]] = []
    detail_successes = 0
    detail_failures = 0
    detail_errors: list[str] = []
    sample_details: list[dict[str, Any]] = []
    for idx, place in enumerate(raw_places):
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
            detail_successes += 1
            if idx < 3:
                sample_details.append({
                    "name": detail.get("name", "?"),
                    "keys": list(detail.keys()),
                    "has_phone": "formatted_phone_number" in detail,
                    "has_website": "website" in detail,
                    "phone_val": detail.get("formatted_phone_number", ""),
                    "website_val": detail.get("website", ""),
                    "status": detail_resp.get("status", "?"),
                })
        except Exception as exc:
            detail_failures += 1
            detail_errors.append(f"{place.get('name', 'unknown')}: {exc}")
            if idx < 3:
                sample_details.append({"name": place.get("name", "?"), "error": str(exc)})
        geom = detail.get("geometry", place.get("geometry", {}))
        types_list = detail.get("types", detail.get("type", place.get("types", [])))
        types_str = ", ".join(
            t.replace("_", " ").title()
            for t in types_list
            if t not in ("establishment", "point_of_interest")
        )
        phone = detail.get("formatted_phone_number", "") or detail.get("international_phone_number", "")
        enriched.append({
            "name": detail.get("name", place.get("name", "")),
            "category": types_str,
            "address": detail.get("formatted_address", place.get("formatted_address", "")),
            "phone": phone,
            "website": detail.get("website", ""),
            "lat": geom.get("location", {}).get("lat", lat),
            "lng": geom.get("location", {}).get("lng", lng),
        })
        time.sleep(DETAILS_DELAY)
    JUNK_NAMES = {"sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.", "malaysia", "(malaysia)", ""}
    before_filter = len(enriched)
    filtered: list[dict[str, Any]] = []
    for e in enriched:
        name = e["name"].strip()
        if len(name) < 3:
            continue
        name_lower = name.lower().strip("() .")
        core = name_lower
        for suffix in ["sdn bhd", "sdn. bhd.", "sdn bhd.", "bhd", "bhd.", "(m)", "(malaysia)", "malaysia"]:
            core = core.replace(suffix, "").strip(" .,()-")
        if not core or core in JUNK_NAMES:
            continue
        filtered.append(e)
    enriched = filtered
    has_phone_count = sum(1 for e in enriched if e["phone"])
    has_website_count = sum(1 for e in enriched if e["website"])
    debug_log.append(f"details: {detail_successes} ok, {detail_failures} failed out of {len(raw_places)}")
    debug_log.append(f"with_phone: {has_phone_count}, with_website: {has_website_count}")
    if before_filter != len(enriched):
        debug_log.append(f"filtered_bad_names: {before_filter - len(enriched)}")
    return JSONResponse(content={
        "places": enriched,
        "count": len(enriched),
        "postcode": postcode,
        "centroid": {"lat": lat, "lng": lng},
        "debug": debug_log,
        "detail_errors": detail_errors[:10] if detail_errors else [],
        "sample_details": sample_details,
    })
