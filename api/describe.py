"""POST /api/describe — AI-powered business description endpoint.

Fetches business websites, extracts visible text, and uses the Claude API to
generate concise one-line descriptions.  Falls back to inference from the
company name, category, and address for businesses without a working website.
"""
from __future__ import annotations

import json
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._shared.constants import BATCH_CAP, MAX_TEXT_PER_SITE

logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------------------------------------------------------------------
# Claude API settings
# ---------------------------------------------------------------------------
_CLAUDE_MODEL = "claude-haiku-4-5-20251001"
_CLAUDE_MAX_TOKENS = 1024
_CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_TIMEOUT = 30.0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_FETCH_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Lightweight HTML-to-text converter that skips script/style tags."""

    _SKIP_TAGS: frozenset[str] = frozenset({
        "script", "style", "noscript", "svg", "path",
    })

    def __init__(self) -> None:
        super().__init__()
        self._texts: list[str] = []
        self._skip: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            cleaned = data.strip()
            if cleaned:
                self._texts.append(cleaned)

    def get_text(self) -> str:
        """Return all extracted text fragments joined by spaces."""
        return " ".join(self._texts)


def _extract_text_from_html(html: str) -> str:
    """Convert raw HTML to truncated plain text.

    Args:
        html: Raw HTML string.

    Returns:
        Cleaned text capped at :data:`~api._shared.constants.MAX_TEXT_PER_SITE`
        characters.  Returns an empty string on parse errors.
    """
    try:
        parser = _TextExtractor()
        parser.feed(html)
        text = parser.get_text()
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_TEXT_PER_SITE]
    except Exception:
        logger.debug("HTML text extraction failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# JSON extraction from Claude responses
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict[str, str]:
    """Robustly extract a JSON object from Claude's response text.

    Tries three strategies in order:

    1. Direct ``json.loads`` on the full text.
    2. Extract from a Markdown code fence (````` ```json ... ``` ``````).
    3. Find the first ``{...}`` block.

    Args:
        text: Raw response text from Claude.

    Returns:
        Parsed JSON dict, or an empty dict if extraction fails.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Markdown code fence
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: first brace-delimited block
    brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return {}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class Business(BaseModel):
    """A single business to describe.

    Attributes:
        name: Business name.
        category: Google Places type category.
        address: Full address string.
        website: Homepage URL (may be empty).
    """

    name: str
    category: str = ""
    address: str = ""
    website: str = ""


class DescribeRequest(BaseModel):
    """Batch description request.

    Attributes:
        businesses: List of businesses to generate descriptions for.
    """

    businesses: list[Business]


# ---------------------------------------------------------------------------
# Claude API helper
# ---------------------------------------------------------------------------
async def _call_claude(api_key: str, prompt: str) -> tuple[str, str | None]:
    """Send a prompt to the Claude API and return the text response.

    Args:
        api_key: Anthropic API key.
        prompt: The user-role prompt text.

    Returns:
        A ``(response_text, error)`` tuple.  On success *error* is ``None``;
        on failure *response_text* is empty and *error* describes the issue.
    """
    async with httpx.AsyncClient(timeout=_CLAUDE_TIMEOUT) as client:
        try:
            resp = await client.post(
                _CLAUDE_API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _CLAUDE_MODEL,
                    "max_tokens": _CLAUDE_MAX_TOKENS,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        except httpx.HTTPError as exc:
            return "", f"claude-http-error: {exc}"

    if resp.status_code != 200:
        return "", f"claude-status-{resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    text = next(
        (block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"),
        "",
    )
    return text, None


# ---------------------------------------------------------------------------
# Website fetching
# ---------------------------------------------------------------------------
async def _fetch_site_texts(
    businesses: list[Business],
) -> dict[int, str]:
    """Fetch and extract text from business websites concurrently.

    Args:
        businesses: The batch of businesses to scrape.

    Returns:
        A dict mapping batch index to extracted text for businesses whose
        websites returned usable content (> 50 chars).
    """
    site_texts: dict[int, str] = {}

    async def _fetch_one(idx: int, url: str, client: httpx.AsyncClient) -> None:
        try:
            if not url.startswith("http"):
                url = "https://" + url
            resp = await client.get(
                url,
                follow_redirects=True,
                timeout=10.0,
                headers=_FETCH_HEADERS,
            )
            if resp.status_code == 200:
                text = _extract_text_from_html(resp.text)
                if len(text) > 50:
                    site_texts[idx] = text
        except httpx.HTTPError:
            logger.debug("Failed to fetch website for index %d: %s", idx, url)
        except Exception:
            logger.debug("Unexpected error fetching %s", url, exc_info=True)

    async with httpx.AsyncClient() as client:
        tasks = [
            _fetch_one(i, b.website, client)
            for i, b in enumerate(businesses)
            if b.website and b.website not in ("", "--")
        ]
        if tasks:
            import asyncio
            await asyncio.gather(*tasks)

    return site_texts


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def _build_website_prompt(
    items: list[tuple[int, Business, str]],
) -> str:
    """Build a Claude prompt for businesses with website content.

    Args:
        items: Tuples of ``(batch_index, business, extracted_text)``.

    Returns:
        Formatted prompt string.
    """
    lines = [
        f"{i + 1}. {b.name} (Website content: {content})"
        for i, b, content in items
    ]
    prompt_content = "\n\n".join(lines)

    return (
        "Based on each company's actual website content below, write a "
        "specific one-line description (max 20 words) of what the business "
        "does — their products, services, or industry.\n"
        f"{prompt_content}\n"
        "Reply with ONLY a JSON object mapping the number to the description. "
        'Example:\n{"1": "Manufactures automotive wire harnesses and cable '
        'assemblies for major car brands", "3": "Produces refined palm oil '
        'and oleochemical products for export"}'
    )


def _build_inference_prompt(
    items: list[tuple[int, Business]],
) -> str:
    """Build a Claude prompt for businesses without website content.

    Args:
        items: Tuples of ``(batch_index, business)``.

    Returns:
        Formatted prompt string.
    """
    lines: list[str] = []
    for i, b in items:
        parts = [b.name]
        if b.category:
            parts.append(f"(category: {b.category})")
        if b.website:
            parts.append(f"(website: {b.website})")
        if b.address:
            parts.append(f"- {b.address}")
        lines.append(f"{i + 1}. {' '.join(parts)}")
    business_list = "\n".join(lines)

    return (
        "For each of these Malaysian companies, write a specific one-line "
        "description (max 20 words) of what the business does — their "
        "products, services, or industry. Use your knowledge of well-known "
        "companies (e.g. Samsung, Intel, Lam Research etc.) and infer from "
        "the company name, category, and location for lesser-known ones.\n"
        f"{business_list}\n"
        "Reply with ONLY a JSON object mapping the number to the description. "
        "Every company MUST have a description.\n"
        'Example: {"1": "Manufactures semiconductor wafer processing '
        'equipment", "2": "Supplies industrial packaging materials"}'
    )


# ---------------------------------------------------------------------------
# Description generation (website-based and inference-based)
# ---------------------------------------------------------------------------
async def _describe_with_website(
    api_key: str,
    items: list[tuple[int, Business, str]],
) -> tuple[dict[str, str], list[str]]:
    """Generate descriptions from website content via Claude.

    Args:
        api_key: Anthropic API key.
        items: Businesses that have scraped website text.

    Returns:
        A ``(descriptions, errors)`` tuple.
    """
    descriptions: dict[str, str] = {}
    errors: list[str] = []

    prompt = _build_website_prompt(items)
    text, error = await _call_claude(api_key, prompt)

    if error:
        errors.append(f"website-claude: {error}")
    elif text:
        raw = _extract_json(text)
        for i, b, _ in items:
            key = str(i + 1)
            if key in raw:
                descriptions[b.name] = raw[key]

    return descriptions, errors


async def _describe_by_inference(
    api_key: str,
    items: list[tuple[int, Business]],
) -> tuple[dict[str, str], list[str]]:
    """Generate descriptions by inference (no website content) via Claude.

    Args:
        api_key: Anthropic API key.
        items: Businesses that lack usable website content.

    Returns:
        A ``(descriptions, errors)`` tuple.
    """
    descriptions: dict[str, str] = {}
    errors: list[str] = []

    prompt = _build_inference_prompt(items)
    text, error = await _call_claude(api_key, prompt)

    if error:
        errors.append(f"inference-claude: {error}")
    elif text:
        raw = _extract_json(text)
        for i, b in items:
            key = str(i + 1)
            if key in raw:
                descriptions[b.name] = raw[key]
        if not raw:
            errors.append(f"inference-parse-fail: {text[:200]}")

    return descriptions, errors


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/describe")
async def describe(body: DescribeRequest) -> JSONResponse:
    """Generate AI-powered business descriptions for a batch of companies.

    The endpoint first scrapes each company's website for content. Companies
    with usable website text get descriptions based on real content; the rest
    are described via Claude's general knowledge.

    Args:
        body: Request containing a list of businesses to describe.

    Returns:
        JSON with ``descriptions``, ``errors``, and ``stats`` fields.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(content={
            "descriptions": {},
            "error": "ANTHROPIC_API_KEY not configured",
        })

    batch = body.businesses[:BATCH_CAP]

    # 1. Fetch website text concurrently
    site_texts = await _fetch_site_texts(batch)

    # 2. Split into "has content" vs. "needs inference"
    with_content: list[tuple[int, Business, str]] = []
    without_content: list[tuple[int, Business]] = []
    for i, b in enumerate(batch):
        if i in site_texts:
            with_content.append((i, b, site_texts[i]))
        else:
            without_content.append((i, b))

    # 3. Generate descriptions (both paths run concurrently)
    descriptions: dict[str, str] = {}
    all_errors: list[str] = []

    if with_content:
        descs, errs = await _describe_with_website(api_key, with_content)
        descriptions.update(descs)
        all_errors.extend(errs)

    if without_content:
        descs, errs = await _describe_by_inference(api_key, without_content)
        descriptions.update(descs)
        all_errors.extend(errs)

    return JSONResponse(content={
        "descriptions": descriptions,
        "errors": all_errors,
        "stats": {
            "with_website": len(with_content),
            "without_website": len(without_content),
            "described": len(descriptions),
            "site_texts_fetched": len(site_texts),
        },
    })
