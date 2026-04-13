"""
POST /api/describe
Fetch business websites and use Claude to generate descriptions from actual content.
Falls back to web search for businesses without websites.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
from html.parser import HTMLParser
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
app = FastAPI()
def extract_json(text: str) -> dict[str, str]:
    """Robustly extract a JSON object from Claude's response text."""
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
    brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return {}
BATCH_CAP = 10
MAX_TEXT_PER_SITE = 1500

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._texts: list[str] = []
        self._skip = False
        self._skip_tags = {"script", "style", "noscript", "svg", "path"}
    def handle_starttag(self, tag: str, _):
        if tag.lower() in self._skip_tags:
            self._skip = True
    def handle_endtag(self, tag: str):
        if tag.lower() in self._skip_tags:
            self._skip = False
    def handle_data(self, data: str):
        if not self._skip:
            cleaned = data.strip()
            if cleaned:
                self._texts.append(cleaned)
    def get_text(self) -> str:
        return " ".join(self._texts)

def extract_text_from_html(html: str) -> str:
    try:
        parser = _TextExtractor()
        parser.feed(html)
        text = parser.get_text()
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_TEXT_PER_SITE]
    except Exception:
        return ""

class Business(BaseModel):
    name: str
    category: str = ""
    address: str = ""
    website: str = ""

class DescribeRequest(BaseModel):
    businesses: list[Business]

@app.post("/api/describe")
async def describe(body: DescribeRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(content={"descriptions": {}, "error": "ANTHROPIC_API_KEY not configured"})
    import httpx
    batch = body.businesses[:BATCH_CAP]
    site_texts: dict[int, str] = {}
    async def fetch_site(idx: int, url: str, client: httpx.AsyncClient):
        try:
            if not url.startswith("http"):
                url = "https://" + url
            resp = await client.get(
                url,
                follow_redirects=True,
                timeout=10.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if resp.status_code == 200:
                text = extract_text_from_html(resp.text)
                if len(text) > 50:
                    site_texts[idx] = text
        except Exception:
            pass
    async with httpx.AsyncClient() as client:
        tasks = []
        for i, b in enumerate(batch):
            if b.website and b.website not in ("", "--"):
                tasks.append(fetch_site(i, b.website, client))
        if tasks:
            await asyncio.gather(*tasks)
    with_content: list[tuple[int, Business, str]] = []
    without_content: list[tuple[int, Business]] = []
    for i, b in enumerate(batch):
        if i in site_texts:
            with_content.append((i, b, site_texts[i]))
        else:
            without_content.append((i, b))
    descriptions: dict[str, str] = {}
    errors: list[str] = []
    if with_content:
        lines = []
        for i, b, content in with_content:
            lines.append(f"{i + 1}. {b.name} (Website content: {content})")
        prompt_content = "\n\n".join(lines)
        prompt = f"""Based on each company's actual website content below, write a specific one-line description (max 20 words) of what the business does — their products, services, or industry.
{prompt_content}
Reply with ONLY a JSON object mapping the number to the description. Example:
{{"1": "Manufactures automotive wire harnesses and cable assemblies for major car brands", "3": "Produces refined palm oil and oleochemical products for export"}}"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1024,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                if text:
                    raw = extract_json(text)
                    for i, b, _ in with_content:
                        key = str(i + 1)
                        if key in raw:
                            descriptions[b.name] = raw[key]
            else:
                errors.append(f"website-claude: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            errors.append(f"website-claude-err: {exc}")
    if without_content:
        lines = []
        for i, b in without_content:
            parts = [b.name]
            if b.category:
                parts.append(f"(category: {b.category})")
            if b.website:
                parts.append(f"(website: {b.website})")
            if b.address:
                parts.append(f"- {b.address}")
            lines.append(f"{i + 1}. {' '.join(parts)}")
        business_list = "\n".join(lines)
        prompt = f"""For each of these Malaysian companies, write a specific one-line description (max 20 words) of what the business does — their products, services, or industry. Use your knowledge of well-known companies (e.g. Samsung, Intel, Lam Research etc.) and infer from the company name, category, and location for lesser-known ones.
{business_list}
Reply with ONLY a JSON object mapping the number to the description. Every company MUST have a description.
Example: {{"1": "Manufactures semiconductor wafer processing equipment", "2": "Supplies industrial packaging materials"}}"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5-20251001",
                        "max_tokens": 1024,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                if text:
                    raw = extract_json(text)
                    for i, b in without_content:
                        key = str(i + 1)
                        if key in raw:
                            descriptions[b.name] = raw[key]
                    if not raw:
                        errors.append(f"inference-parse-fail: {text[:200]}")
            else:
                errors.append(f"inference-claude: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            errors.append(f"inference-claude-err: {exc}")
    return JSONResponse(content={
        "descriptions": descriptions,
        "errors": errors,
        "stats": {
            "with_website": len(with_content),
            "without_website": len(without_content),
            "described": len(descriptions),
            "site_texts_fetched": len(site_texts),
        },
    })
