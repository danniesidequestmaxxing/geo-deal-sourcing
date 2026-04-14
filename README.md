# Malaysia PE Deal Sourcer

Web-based tool for identifying manufacturing and industrial acquisition targets
in Malaysia by postcode. Searches Google Places, estimates building footprints
via OpenStreetMap, and generates AI-powered business descriptions.

## How It Works

1. Enter a 5-digit Malaysian postcode (e.g. `40000` for Shah Alam) or company name.
2. The backend geocodes the input and searches Google Places for factories,
   manufacturers, and industrial facilities within 5 km.
3. Each facility's building footprint is estimated via OpenStreetMap (multiple
   Overpass servers with failover + Redis caching).
4. Claude AI generates a one-line business description from the company's website
   content (or infers one from the name/category).
5. Results are displayed with estimated square footage, size tier, and revenue proxy.
6. Export the full dataset as a formatted Excel file, or save/load searches via Redis.

## Architecture

```
app/                    Next.js frontend (App Router)
  layout.tsx            Root layout
  page.tsx              Main single-page application

api/                    Python serverless functions (Vercel)
  search.py             POST /api/search   — Google Places discovery
  enrich.py             POST /api/enrich   — Overpass building footprints
  describe.py           POST /api/describe — Claude AI descriptions
  export.py             POST /api/export   — Excel workbook generation
  saves.py              GET|POST|DELETE /api/saves — Redis CRUD

  _shared/              Shared Python utilities (not deployed as endpoints)
    constants.py        Centralised configuration values
    geometry.py         Shoelace polygon area + Overpass client
    google_maps.py      Google Maps client factory
    redis_client.py     Redis connection management (context manager)
    excel.py            Styled Excel workbook generation

malaysia_sourcer.py     Standalone CLI tool (same pipeline, offline use)
```

## Tech Stack

| Layer      | Technology                                                  |
|------------|-------------------------------------------------------------|
| Frontend   | Next.js 15 (App Router), React 19, Tailwind CSS v4         |
| Backend    | Python 3.10+, FastAPI (Vercel Serverless Functions)         |
| APIs       | Google Places API, Overpass API (multi-server failover)     |
| AI         | Claude API (Haiku) for business descriptions                |
| Caching    | Redis (saved searches + building footprint cache)           |
| Export     | openpyxl (formatted Excel workbooks)                        |

## Prerequisites

- **Node.js** >= 18
- **Python** >= 3.10
- **Google Maps API key** with Places API and Geocoding API enabled
- **Anthropic API key** for AI-generated business descriptions
- **Redis** (optional) for saved searches and footprint caching

## Local Development

```bash
# 1. Install dependencies
npm install
pip install -r requirements.txt

# 2. Set environment variables
export GOOGLE_MAPS_API_KEY=your_key_here
export ANTHROPIC_API_KEY=your_key_here
export REDIS_URL=redis://localhost:6379          # optional

# 3. Start the development server
npx vercel dev
```

The app will be available at `http://localhost:3000`.

### CLI Tool

The standalone CLI performs the same pipeline offline and writes an Excel file:

```bash
# Search by postcodes
python malaysia_sourcer.py --postcodes 40000 40100 40150

# Search from a file (one postcode per line, # comments supported)
python malaysia_sourcer.py --file postcodes.txt

# With explicit API key
python malaysia_sourcer.py --postcodes 40000 --api-key YOUR_KEY
```

## Deployment on Vercel

### 1. Push to GitHub

```bash
git add .
git commit -m "Initial commit"
git push -u origin main
```

### 2. Import to Vercel

1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your GitHub repository
3. Framework Preset: **Next.js**
4. Click **Deploy**

### 3. Add Environment Variables

In Vercel project settings (**Settings > Environment Variables**):

| Variable              | Required | Description                              |
|-----------------------|----------|------------------------------------------|
| `GOOGLE_MAPS_API_KEY` | Yes      | Google Maps API key (Places + Geocoding) |
| `ANTHROPIC_API_KEY`   | Yes      | Anthropic API key for Claude             |
| `REDIS_URL`           | No       | Redis connection URL for persistence     |

## Project Conventions

- **Python**: PEP 8, type hints on all signatures, Google-style docstrings.
- **Shared code** lives in `api/_shared/` (prefixed with `_` so Vercel skips it).
- **Constants** are centralised in `api/_shared/constants.py`.
- **Logging** via `logging` module (not `print()`).
- **Resource management**: Redis connections use the `redis_connection()` context
  manager from `api/_shared/redis_client.py`.
