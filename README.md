# Malaysia PE Deal Sourcer
Web-based tool for identifying manufacturing and industrial acquisition targets in Malaysia by postcode.

## How It Works
1. Enter a 5-digit Malaysian postcode (e.g. `40000` for Shah Alam)
2. Geocodes the postcode and searches Google Places for factories, manufacturers, and industrial facilities within 5km
3. Each facility's building footprint is estimated via OpenStreetMap (multiple Overpass servers with failover + Redis caching)
4. Results are displayed with estimated square footage, size tier, and revenue proxy
5. Export the full dataset as a formatted Excel file

## Tech Stack
- **Frontend:** Next.js 15 (App Router) + Tailwind CSS v4
- **Backend:** Python (FastAPI) on Vercel Serverless Functions
- **APIs:** Google Places API, Overpass API (multi-server failover), Claude AI (business descriptions)
- **Caching:** Redis (saved searches + building footprint cache)

## Deployment on Vercel (via GitHub)

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/malaysia-deal-sourcer.git
git push -u origin main
```

### 2. Import to Vercel
1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your GitHub repository
3. Framework Preset: **Next.js**
4. Click **Deploy**

### 3. Add Environment Variables
In Vercel project → **Settings > Environment Variables**:
- `GOOGLE_MAPS_API_KEY` — your Google Maps API key
- `ANTHROPIC_API_KEY` — your Anthropic API key
- `REDIS_URL` — your Redis connection URL (for saved searches)

## Local Development
```bash
npm install
pip install -r requirements.txt
export GOOGLE_MAPS_API_KEY=your_key_here
export ANTHROPIC_API_KEY=your_key_here
npx vercel dev
```
