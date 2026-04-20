#!/usr/bin/env python3
"""Malaysia Manufacturing Deal Sourcer — CLI tool.

Identifies manufacturing / factory acquisition targets in Malaysia by
postcode.  Uses Google Places API for discovery, OpenStreetMap (Overpass)
for building-footprint estimation, and homepage keyword scraping.

Usage::

    python malaysia_sourcer.py --postcodes 40000 40100 40150
    python malaysia_sourcer.py --postcodes 40000 40100 --api-key YOUR_KEY
    python malaysia_sourcer.py --file postcodes.txt
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import googlemaps
import requests
from bs4 import BeautifulSoup

from api._shared.constants import SQ_M_TO_SQ_FT, USER_AGENT, WEB_SCRAPE_TIMEOUT
from api._shared.excel import create_workbook
from api._shared.geometry import classify_size_tier, estimate_building_sqft
from api._shared.google_maps import geocode_postcode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLI-specific constants
# ---------------------------------------------------------------------------
SEARCH_KEYWORDS: list[str] = [
    "factory",
    "manufacturing",
    "industrial estate",
    "kilang",
    "manufacturer",
    "industrial park",
]

PLACES_DELAY: float = 0.25
DETAILS_DELAY: float = 0.15
OVERPASS_DELAY: float = 1.0
WEB_SCRAPE_DELAY: float = 0.3

HOMEPAGE_KEYWORDS: list[str] = [
    "employees", "annual capacity", "production capacity",
    "workforce", "headcount", "staff", "revenue",
    "square feet", "square meters", "sq ft", "sq m",
    "ISO 9001", "ISO 14001", "IATF",
]

# ---------------------------------------------------------------------------
# Excel column layout (CLI-specific, differs from the web export)
# ---------------------------------------------------------------------------
CLI_COLUMNS: list[tuple[str, int]] = [
    ("No.", 6),
    ("Company Name", 38),
    ("Category / Types", 28),
    ("Full Address", 48),
    ("Phone", 20),
    ("Website", 34),
    ("Sq Ft Estimate", 16),
    ("Size Tier", 13),
    ("Homepage Keywords", 32),
    ("Google Maps Link", 42),
]

_CLI_SQFT_COLUMN = 7  # 1-based index of "Sq Ft Estimate"


# ---------------------------------------------------------------------------
# Google Maps helpers
# ---------------------------------------------------------------------------
def _search_places_for_postcode(
    gmaps: googlemaps.Client,
    postcode: str,
    lat: float,
    lng: float,
) -> list[dict[str, Any]]:
    """Search Google Places for manufacturing businesses near a postcode.

    Iterates over :data:`SEARCH_KEYWORDS`, deduplicates by ``place_id``,
    and follows pagination tokens.

    Args:
        gmaps: Authenticated Google Maps client.
        postcode: The target postcode.
        lat: Latitude of the postcode centroid.
        lng: Longitude of the postcode centroid.

    Returns:
        Deduplicated list of raw Place results.
    """
    seen_ids: set[str] = set()
    results: list[dict[str, Any]] = []

    for keyword in SEARCH_KEYWORDS:
        query = f"{keyword} {postcode} Malaysia"
        try:
            resp = gmaps.places(query=query, location=(lat, lng), radius=5000)
        except Exception as exc:
            logger.warning("Places search error (%s): %s", keyword, exc)
            time.sleep(PLACES_DELAY)
            continue

        for place in resp.get("results", []):
            pid = place["place_id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                results.append(place)

        # Follow pagination
        while resp.get("next_page_token"):
            time.sleep(2)
            try:
                resp = gmaps.places(
                    query=query, page_token=resp["next_page_token"],
                )
                for place in resp.get("results", []):
                    pid = place["place_id"]
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        results.append(place)
            except Exception:
                logger.debug("Pagination failed for keyword %s", keyword)
                break

        time.sleep(PLACES_DELAY)

    return results


def _enrich_place(
    gmaps: googlemaps.Client,
    place_id: str,
) -> dict[str, Any]:
    """Fetch Place Details for a single place.

    Args:
        gmaps: Authenticated Google Maps client.
        place_id: Google Place ID.

    Returns:
        Place Details dict, or an empty dict on failure.
    """
    fields = [
        "name", "formatted_address", "formatted_phone_number",
        "website", "geometry", "types", "business_status", "url",
    ]
    try:
        detail = gmaps.place(place_id=place_id, fields=fields)
        return detail.get("result", {})
    except Exception as exc:
        logger.warning("Details fetch failed for %s: %s", place_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Homepage keyword scraping
# ---------------------------------------------------------------------------
def _scrape_homepage_keywords(url: str) -> list[str]:
    """Scrape a business homepage and return matching keywords.

    Args:
        url: The homepage URL to scrape.

    Returns:
        List of :data:`HOMEPAGE_KEYWORDS` found on the page.
    """
    if not url:
        return []
    try:
        resp = requests.get(
            url,
            timeout=WEB_SCRAPE_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(separator=" ", strip=True).lower()
        return [kw for kw in HOMEPAGE_KEYWORDS if kw.lower() in text]
    except requests.RequestException as exc:
        logger.debug("Homepage scrape failed for %s: %s", url, exc)
        return []
    except Exception:
        logger.debug("Unexpected error scraping %s", url, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Excel row value extractor
# ---------------------------------------------------------------------------
def _cli_row_values(row_index: int, row_data: dict[str, Any]) -> list[Any]:
    """Extract ordered cell values for the CLI Excel layout.

    Args:
        row_index: 1-based row number for the "No." column.
        row_data: Lead data dict.

    Returns:
        List of values matching :data:`CLI_COLUMNS`.
    """
    return [
        row_index,
        row_data.get("name", ""),
        row_data.get("category", ""),
        row_data.get("address", ""),
        row_data.get("phone", ""),
        row_data.get("website", ""),
        row_data.get("sqft"),
        row_data.get("size_tier", ""),
        row_data.get("homepage_keywords", ""),
        row_data.get("maps_link", ""),
    ]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def _build_lead(
    detail: dict[str, Any],
    place: dict[str, Any],
    lat: float,
    lng: float,
) -> dict[str, Any]:
    """Assemble a single lead record from place/detail data.

    Estimates building footprint, scrapes the homepage for keywords, and
    classifies the size tier.

    Args:
        detail: Enriched Place Details dict.
        place: Original raw Place result.
        lat: Fallback latitude.
        lng: Fallback longitude.

    Returns:
        A fully-populated lead dict.
    """
    geom = detail.get("geometry", place.get("geometry", {}))
    plat = geom.get("location", {}).get("lat", lat)
    plng = geom.get("location", {}).get("lng", lng)

    sqft = estimate_building_sqft(plat, plng)
    time.sleep(1.0)

    website = detail.get("website", "")
    kw_found = _scrape_homepage_keywords(website) if website else []
    if website:
        time.sleep(WEB_SCRAPE_DELAY)

    types_list = detail.get("types", place.get("types", []))
    category = ", ".join(
        t.replace("_", " ").title()
        for t in types_list
        if t != "establishment"
    )

    return {
        "name": detail.get("name", place.get("name", "")),
        "category": category,
        "address": detail.get("formatted_address", ""),
        "phone": detail.get("formatted_phone_number", ""),
        "website": website,
        "sqft": round(sqft) if sqft else None,
        "size_tier": classify_size_tier(sqft),
        "homepage_keywords": ", ".join(kw_found),
        "maps_link": detail.get("url", ""),
    }


def _deduplicate_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate leads by (name, address) and sort by footprint size.

    Args:
        leads: Unfiltered lead list (may contain duplicates).

    Returns:
        Deduplicated list sorted largest-footprint-first, then by name.
    """
    seen_keys: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for lead in leads:
        key = (lead["name"].lower().strip(), lead["address"].lower().strip())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(lead)
    deduped.sort(key=lambda r: (r["sqft"] is None, -(r["sqft"] or 0), r["name"]))
    return deduped


def run(postcodes: list[str], api_key: str) -> None:
    """Execute the full sourcing pipeline.

    Geocodes each postcode, searches for manufacturing businesses, enriches
    each result, and writes the output to an Excel file.

    Args:
        postcodes: List of 5-digit Malaysian postcode strings.
        api_key: Google Maps API key.
    """
    gmaps = googlemaps.Client(key=api_key)
    all_leads: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for pc_idx, postcode in enumerate(postcodes, start=1):
        postcode = postcode.strip()
        if not re.fullmatch(r"\d{5}", postcode):
            logger.info("Skipping invalid postcode: '%s'", postcode)
            continue

        logger.info("[%d/%d] Processing: %s", pc_idx, len(postcodes), postcode)
        latlng = geocode_postcode(gmaps, postcode)
        if not latlng:
            continue
        lat, lng = latlng

        places = _search_places_for_postcode(gmaps, postcode, lat, lng)
        logger.info("  Found %d unique places", len(places))

        for i, place in enumerate(places):
            pid = place["place_id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            logger.info(
                "  [%d/%d] Enriching: %s",
                i + 1, len(places), place.get("name", "Unknown"),
            )
            detail = _enrich_place(gmaps, pid)
            time.sleep(DETAILS_DELAY)
            if not detail:
                continue

            lead = _build_lead(detail, place, lat, lng)
            all_leads.append(lead)

    deduped = _deduplicate_leads(all_leads)

    if not deduped:
        logger.info("No leads found.")
        return

    output_path = Path.cwd() / f"Source_Leads_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    wb = create_workbook(
        columns=CLI_COLUMNS,
        rows=deduped,
        value_extractor=_cli_row_values,
        sheet_title="Manufacturing Leads",
        number_format_columns={_CLI_SQFT_COLUMN},
    )
    wb.save(output_path)
    logger.info("Complete: %d leads -> %s", len(deduped), output_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Parse command-line arguments and run the sourcing pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Malaysia Manufacturing Deal Sourcer",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--postcodes", "-p", nargs="+", help="5-digit postcodes")
    group.add_argument("--file", "-f", type=str, help="File with one postcode per line")
    parser.add_argument("--api-key", "-k", type=str, default=None, help="Google Maps API key")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        logger.error(
            "Provide a Google Maps API key via --api-key or GOOGLE_MAPS_API_KEY env var."
        )
        sys.exit(1)

    if args.file:
        path = Path(args.file)
        if not path.exists():
            logger.error("File not found: %s", path)
            sys.exit(1)
        postcodes = [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    else:
        postcodes = args.postcodes

    run(postcodes, api_key)


if __name__ == "__main__":
    main()
