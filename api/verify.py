"""POST /api/verify — Lead verification endpoint.

Performs three free checks on each lead to produce a confidence score:

1. **Business status** — is the Google Places ``business_status`` set to
   ``OPERATIONAL``?
2. **Website liveness + name match** — does the website resolve, and does
   the company name appear on the homepage?
3. **Phone validation** — is the phone number a structurally valid Indonesian
   number?
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
import phonenumbers
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._shared.constants import BATCH_CAP, USER_AGENT

logger = logging.getLogger(__name__)

app = FastAPI()

_PHONE_DEFAULT_REGION = "ID"
_WEBSITE_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class LeadToVerify(BaseModel):
    """A single lead to verify.

    Attributes:
        name: Company name.
        phone: Phone number string (may be empty).
        website: Homepage URL (may be empty).
        business_status: Google Places business status (may be empty).
    """

    name: str
    phone: str = ""
    website: str = ""
    business_status: str = ""


class VerifyRequest(BaseModel):
    """Batch verification request.

    Attributes:
        leads: List of leads to check (capped at :data:`BATCH_CAP`).
    """

    leads: list[LeadToVerify]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_business_status(status: str) -> dict[str, Any]:
    """Check whether the business is marked as operational.

    Args:
        status: The ``business_status`` string from Google Places.

    Returns:
        Dict with ``status`` and ``pass`` keys.
    """
    if not status:
        return {"status": "", "pass": False, "available": False}
    is_operational = status.upper() == "OPERATIONAL"
    return {"status": status, "pass": is_operational, "available": True}


def _check_phone(phone: str) -> dict[str, Any]:
    """Validate a phone number as a structurally valid Indonesian number.

    Args:
        phone: Raw phone string from Google Places.

    Returns:
        Dict with ``valid``, ``formatted``, and ``pass`` keys.
    """
    if not phone or not phone.strip():
        return {"valid": False, "formatted": "", "pass": False, "available": False}
    try:
        parsed = phonenumbers.parse(phone, _PHONE_DEFAULT_REGION)
        is_valid = phonenumbers.is_valid_number(parsed)
        formatted = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL,
        )
        return {
            "valid": is_valid,
            "formatted": formatted,
            "pass": is_valid,
            "available": True,
        }
    except phonenumbers.NumberParseException:
        return {"valid": False, "formatted": phone, "pass": False, "available": True}


def _normalise_name(name: str) -> str:
    """Strip common Indonesian suffixes for fuzzy name matching.

    Args:
        name: Raw company name.

    Returns:
        Lowercased, suffix-stripped name core.
    """
    core = name.lower().strip()
    for suffix in ["pt", "pt.", "tbk", "tbk.", "(indonesia)", "indonesia", "persero"]:
        core = core.replace(suffix, "")
    return re.sub(r"[^a-z0-9 ]", "", core).strip()


async def _check_website(
    name: str,
    url: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Check website liveness and whether the company name appears on it.

    Args:
        name: Company name to search for on the page.
        url: Homepage URL.
        client: Shared async HTTP client.

    Returns:
        Dict with ``reachable``, ``name_match``, and ``pass`` keys.
    """
    if not url or url in ("", "--"):
        return {
            "reachable": False,
            "name_match": False,
            "pass": False,
            "available": False,
        }

    if not url.startswith("http"):
        url = "https://" + url

    try:
        resp = await client.get(
            url,
            follow_redirects=True,
            timeout=_WEBSITE_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        reachable = resp.status_code == 200

        name_match = False
        if reachable:
            page_text = resp.text.lower()
            core_name = _normalise_name(name)
            # Check if meaningful words from the company name appear
            words = [w for w in core_name.split() if len(w) >= 3]
            if words:
                matches = sum(1 for w in words if w in page_text)
                name_match = matches >= max(1, len(words) // 2)

        return {
            "reachable": reachable,
            "name_match": name_match,
            "pass": reachable and name_match,
            "available": True,
        }
    except (httpx.HTTPError, Exception) as exc:
        logger.debug("Website check failed for %s: %s", url, exc)
        return {
            "reachable": False,
            "name_match": False,
            "pass": False,
            "available": True,
        }


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
def _compute_confidence(checks: dict[str, Any]) -> str:
    """Compute an overall confidence label from individual check results.

    Args:
        checks: Dict with ``business_status``, ``website_liveness``, and
            ``phone_valid`` sub-dicts, each having a ``pass`` and
            ``available`` key.

    Returns:
        One of ``"high"``, ``"medium"``, ``"low"``, or ``"unverified"``.
    """
    available_checks = [
        v for v in [checks["business_status"], checks["website_liveness"], checks["phone_valid"]]
        if v.get("available", False)
    ]

    if not available_checks:
        return "unverified"

    passed = sum(1 for c in available_checks if c["pass"])
    total = len(available_checks)

    if passed == total:
        return "high"
    if passed >= total / 2:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/verify")
async def verify(body: VerifyRequest) -> JSONResponse:
    """Verify a batch of leads and return per-lead confidence scores.

    For each lead, three checks are performed:

    - Business status (from Google Places data already available)
    - Website liveness + company name presence on homepage
    - Phone number validation (Indonesian format)

    Args:
        body: Request containing leads to verify.

    Returns:
        JSON with a ``results`` list, each containing the lead name and
        a ``verification`` object with per-check details and an overall
        ``confidence`` label.
    """
    batch = body.leads[:BATCH_CAP]
    results: list[dict[str, Any]] = []

    # Run all website checks concurrently
    async with httpx.AsyncClient() as client:
        website_tasks = [
            _check_website(lead.name, lead.website, client)
            for lead in batch
        ]
        website_results = await asyncio.gather(*website_tasks)

    for i, lead in enumerate(batch):
        status_check = _check_business_status(lead.business_status)
        phone_check = _check_phone(lead.phone)
        website_check = website_results[i]

        verification = {
            "business_status": status_check,
            "website_liveness": website_check,
            "phone_valid": phone_check,
        }
        verification["confidence"] = _compute_confidence(verification)

        results.append({
            "name": lead.name,
            "verification": verification,
        })

    return JSONResponse(content={"results": results})
