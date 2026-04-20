# Malaysia PE Deal Sourcer

Web-based tool for identifying manufacturing and industrial acquisition targets
in Malaysia. Searches Google Places, estimates building footprints via
OpenStreetMap, and generates AI-powered business descriptions.

## How It Works

```
User Input (postcode / company name / drawn polygon)
        │
        ▼
   ┌─────────┐     Google Places API (10 keyword searches,
   │  SEARCH  │───► deduplicated, junk-filtered, postcode-matched)
   └─────────┘
        │
        ▼
   ┌─────────┐     Overpass 80m → Overpass 200m → Google viewport
   │ ENRICH  │───► → Category default.  Redis-cached for 7 days.
   └─────────┘
        │
        ▼
   ┌──────────┐    Scrape homepage → Claude AI one-liner.
   │ DESCRIBE │───► Falls back to inference from name/category.
   └──────────┘
        │
        ▼
   ┌──────────┐    Business status + website liveness + phone
   │  VERIFY  │───► validation.  Produces confidence score.
   └──────────┘
        │
        ▼
   Results table with export to Excel / save to Redis
```

### Three search modes

| Mode | Input | How it works |
|------|-------|-------------|
| **Postcode** | 5-digit Malaysian postcode | Geocode → 5 km radius text search → filter by postcode |
| **Company** | Company name | Text search across Malaysia |
| **Draw Area** | Polygon on Google Maps | Centroid + radius → Nearby Search → point-in-polygon filter |

## Architecture

```
app/                        Next.js 15 frontend (App Router)
  layout.tsx                Root layout + metadata
  page.tsx                  Main SPA — search, results table, export, saves
  PolygonMap.tsx             Google Maps polygon-drawing component
  globals.css               Tailwind v4 imports + custom animations

api/                        Python serverless functions (Vercel)
  search.py                 POST /api/search   — Google Places discovery
  enrich.py                 POST /api/enrich   — building footprint estimation
  describe.py               POST /api/describe — Claude AI descriptions
  verify.py                 POST /api/verify   — lead confidence scoring
  export.py                 POST /api/export   — Excel workbook generation
  saves.py                  GET|POST|DELETE /api/saves — Redis CRUD

  _shared/                  Shared utilities (not deployed as endpoints)
    constants.py            All magic numbers and config values
    geometry.py             Shoelace area, Overpass client, size tiers
    google_maps.py          Google Maps client factory + geocoding
    redis_client.py         Redis connection context manager
    excel.py                Styled Excel workbook generator

malaysia_sourcer.py         Standalone CLI tool (offline, no web server)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15 (App Router), React 19, Tailwind CSS v4 |
| Map | Google Maps JavaScript API (`@googlemaps/js-api-loader`) |
| Backend | Python 3.10+, FastAPI (Vercel Serverless Functions) |
| APIs | Google Places API, Overpass API (3-server failover) |
| AI | Claude API (Haiku) for business descriptions |
| Caching | Redis (building footprints + saved searches) |
| Export | openpyxl (formatted Excel workbooks) |

## API Reference

### `POST /api/search`

Discover businesses via Google Places.

**Request body:**
```json
{
  "mode": "postcode | company | polygon",
  "postcode": "40000",
  "company": "Perodua",
  "polygon": [[3.1, 101.6], [3.1, 101.7], [3.0, 101.7]]
}
```

**Response:**
```json
{
  "places": [
    {
      "name": "ABC Manufacturing Sdn Bhd",
      "category": "Factory, Industrial",
      "address": "Lot 5, ..., 40000 Shah Alam",
      "phone": "+60 3-5191 1234",
      "website": "https://abc.com",
      "lat": 3.0856,
      "lng": 101.5450,
      "place_id": "ChIJ...",
      "business_status": "OPERATIONAL",
      "viewport": { ... },
      "postcode": "40000"
    }
  ],
  "count": 12,
  "postcode": "40000",
  "centroid": { "lat": 3.0856, "lng": 101.545 },
  "debug": ["factory: status=OK, results=8", ...]
}
```

### `POST /api/enrich`

Estimate building footprint for a single place.

**Request body:**
```json
{
  "lat": 3.0856,
  "lng": 101.545,
  "viewport": { "northeast": { "lat": 3.087, "lng": 101.547 }, "southwest": { "lat": 3.084, "lng": 101.543 } },
  "business_type": "Factory"
}
```

**Response:**
```json
{
  "sqft": 32500,
  "source": "overpass_80m",
  "size_tier": "Medium",
  "revenue_proxy": 4875000
}
```

Source values: `overpass_80m`, `overpass_200m`, `viewport`, `category`.

### `POST /api/describe`

Generate AI descriptions for businesses.

**Request body:**
```json
{
  "leads": [
    { "name": "ABC Sdn Bhd", "category": "Factory", "address": "...", "website": "https://abc.com" }
  ]
}
```

**Response:**
```json
{
  "descriptions": [
    { "description": "Precision metal stamping manufacturer supplying automotive OEMs across Southeast Asia." }
  ]
}
```

### `POST /api/verify`

Verify lead quality with confidence scoring.

**Request body:**
```json
{
  "leads": [
    { "name": "ABC Sdn Bhd", "phone": "+60312345678", "website": "https://abc.com", "business_status": "OPERATIONAL" }
  ]
}
```

**Response:**
```json
{
  "results": [
    {
      "status_ok": true,
      "website_live": true,
      "name_match": true,
      "phone_valid": true,
      "confidence": "high"
    }
  ]
}
```

### `POST /api/export`

Generate a styled Excel workbook from results.

### `GET /api/saves`

List all saved searches from Redis.

### `POST /api/saves`

Save a search (name + places array) to Redis.

### `DELETE /api/saves?name=search_name`

Delete a saved search.

## Environment Variables

| Variable | Required | Where | Purpose |
|----------|----------|-------|---------|
| `GOOGLE_MAPS_API_KEY` | Yes | Backend | Google Places + Geocoding API |
| `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` | Yes | Frontend | Google Maps JavaScript API (map tiles) |
| `ANTHROPIC_API_KEY` | Yes | Backend | Claude AI descriptions |
| `REDIS_URL` | No | Backend | Saved searches + footprint caching |

**Google Cloud API setup:**

The `GOOGLE_MAPS_API_KEY` needs these APIs enabled:
- Places API
- Geocoding API

The `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` needs:
- Maps JavaScript API

These can be the same key or separate keys. Since `NEXT_PUBLIC_*` keys are
exposed to the browser, consider restricting the frontend key by HTTP referrer
(`*.vercel.app/*`) and limiting it to Maps JavaScript API only.

## Local Development

```bash
# 1. Install dependencies
npm install
pip install -r requirements.txt

# 2. Set environment variables
export GOOGLE_MAPS_API_KEY=your_key
export NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=your_key
export ANTHROPIC_API_KEY=your_key
export REDIS_URL=redis://localhost:6379  # optional

# 3. Start the development server
npx vercel dev
```

The app runs at `http://localhost:3000`.

## CLI Tool

The standalone CLI runs the same pipeline offline and outputs an Excel file:

```bash
# Search by postcodes
python malaysia_sourcer.py --postcodes 40000 40100 40150

# From a file (one postcode per line, # comments allowed)
python malaysia_sourcer.py --file postcodes.txt

# With explicit API key
python malaysia_sourcer.py --postcodes 40000 --api-key YOUR_KEY
```

The CLI uses the same shared utilities as the web API (`api/_shared/`) but has
its own rate-limiting delays (slower, safer for batch runs) and search keywords
(factory-focused rather than broad industrial).

## Deployment on Vercel

1. Push to GitHub
2. Import at [vercel.com/new](https://vercel.com/new) → Framework: **Next.js**
3. Add all environment variables (see table above) — make sure to check
   **Production**, **Preview**, and **Development** for each
4. Deploy — Vercel auto-detects the Python serverless functions in `api/`

After adding or changing an env var, you must **redeploy** for it to take effect
(`NEXT_PUBLIC_*` vars are baked in at build time).

## Sqft Estimation Fallback Chain

Building footprints are estimated using a 4-step fallback:

1. **Overpass 80m** — Query OSM for building polygons within 80m of the
   coordinates. Calculate area with the shoelace formula. Most accurate.
2. **Overpass 200m** — Widen the search radius. Catches buildings with
   slightly offset coordinates.
3. **Google viewport** — Use the Place Details `viewport` bounds, apply a
   35% building ratio, cap at 2M sqft.
4. **Category default** — Look up the business type in a table of typical
   Malaysian industrial building sizes (e.g. factory → 45,000 sqft).

Results are cached in Redis for 7 days. Stale entries (where sqft is still
null) are automatically re-estimated on the next request.

## Project Conventions

- **Python**: PEP 8, type hints on all signatures, Google-style docstrings
- **Shared code** lives in `api/_shared/` (prefixed with `_` so Vercel skips it)
- **Constants** centralised in `api/_shared/constants.py` — no magic numbers
- **Logging** via `logging` module (never `print()`)
- **Redis**: use `redis_connection()` context manager from `api/_shared/redis_client.py`
- **Error handling**: validate at system boundaries (user input, external APIs), trust internal code

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Map shows "For development purposes only" | `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` not set or Maps JavaScript API not enabled | Add the env var + enable the API in Google Cloud Console |
| Map shows "Oops! Something went wrong" | API key restrictions block the domain | Add `*.vercel.app/*` to HTTP referrer allowlist, add Maps JavaScript API to API restrictions |
| "No results found" on Draw Area | Google Places returned results outside polygon | Polygon search uses Nearby Search (hard boundary) — try drawing a larger area |
| Sqft shows as N/A | All 4 fallback methods failed and cache has stale entry | Clear Redis cache or wait for TTL expiry (7 days) |
| Vercel build fails | Missing env var or dependency | Check Vercel build logs; ensure `requirements.txt` is up to date |
| Console shows `ApiTargetBlockedMapError` | API key restricted to wrong APIs | Add Maps JavaScript API to the key's allowed API list in Google Cloud |
