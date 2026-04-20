# CLAUDE.md

Project-specific context for Claude Code sessions.

## What this project does

Malaysia PE Deal Sourcer — identifies manufacturing/industrial acquisition
targets in Malaysia. Web UI + Python backend + standalone CLI.

## Stack

- **Frontend**: Next.js 15 (App Router), React 19, Tailwind CSS v4, Google Maps JS API
- **Backend**: Python 3.10+ FastAPI serverless functions on Vercel
- **APIs**: Google Places, Overpass (OSM), Claude AI (Haiku)
- **Caching**: Redis (optional, graceful degradation)

## Key architecture decisions

- `api/_shared/` uses underscore prefix so Vercel doesn't deploy it as an endpoint
- `PolygonMap.tsx` is loaded with `next/dynamic({ ssr: false })` because Google Maps requires the DOM
- Polygon search uses `gmaps.places_nearby()` (hard radius boundary), NOT `gmaps.places()` (soft bias)
- Postcode search filters results by address postcode match (Google location bias is unreliable)
- Sqft estimation has 4-step fallback: Overpass 80m → 200m → viewport → category default
- All constants live in `api/_shared/constants.py` — no magic numbers elsewhere
- User-Agent string is shared via `constants.USER_AGENT`

## Common tasks

### Add a new search keyword
Edit `SEARCH_KEYWORDS` in `api/search.py` (web) or `malaysia_sourcer.py` (CLI).

### Add a new API endpoint
Create `api/new_endpoint.py` with a FastAPI app. Vercel auto-deploys it at `/api/new_endpoint`.

### Modify the results table
Edit `app/page.tsx` — the table rendering starts around the `places.map()` block.

### Change sqft estimation logic
Edit `api/_shared/geometry.py` for the estimation functions.
Edit `api/_shared/constants.py` for thresholds, defaults, and category mappings.

## Testing

No test suite exists. Verify changes by:
1. `npx tsc --noEmit` (TypeScript check)
2. `npx vercel dev` (local dev server)
3. Test all three search modes: postcode, company name, draw area
4. Check Vercel preview deployment after push

## Environment variables

- `GOOGLE_MAPS_API_KEY` — backend (Places + Geocoding)
- `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` — frontend (Maps JavaScript API, exposed to browser)
- `ANTHROPIC_API_KEY` — Claude AI descriptions
- `REDIS_URL` — optional, for caching and saved searches

## Git workflow

- Feature branches from `main`
- Squash merge PRs
- Vercel auto-deploys from `main` (production) and PR branches (preview)

## Files to know

| File | Why it matters |
|------|---------------|
| `app/page.tsx` | ~800 lines, the entire frontend UI |
| `api/search.py` | 3 search modes, place enrichment, junk filtering |
| `api/_shared/constants.py` | Single source of truth for all config values |
| `api/_shared/geometry.py` | Sqft estimation, Overpass queries, size tiers |
| `malaysia_sourcer.py` | CLI tool — shares `api/_shared/` but has own keywords/delays |
