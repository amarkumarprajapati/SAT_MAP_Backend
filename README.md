# LTmap FastAPI Backend

High-performance Python backend powering the LTmap geospatial & OSINT dashboard. All 3rd-party data feeds, real-time SGP4 orbital propagation, AIS maritime WebSocket streams, and OSINT geocoding have been consolidated here.

---

## Quick Start

### 1. Prerequisites
- **Python 3.10+** (Python 3.10 or 3.12 recommended)
- `pip` package manager

### 2. Navigate to Backend Directory
```bash
cd backend
```

### 3. (Optional) Create & Activate a Virtual Environment
**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```
*(On Windows with Python Launcher, you can also run: `py -3.10 -m pip install -r requirements.txt`)*

### 5. Configure Environment (`.env`)
The backend reads from `backend/.env`. A configured `.env` file is already provided:
```env
# Server Binding
HOST=0.0.0.0
PORT=8000
BASE_URL=http://localhost:8000
API_PREFIX=/api
TIMEOUT_SECONDS=30.0

# AIS Stream (Live Maritime Ship Tracking)
AIS_API_KEY=5deb54fff0481c85565cc4ddc9883875500ffc6e

# OpenSky Network (Optional, for higher military flight rate limits)
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=

# NASA FIRMS (Optional)
FIRMS_API_KEY=
```

---

## Running the Server

### Option A: Using `run.py` (Recommended)
```powershell
# Standard run
python run.py

# With hot-reload for active development
python run.py --reload
```
*(If using the Windows Python launcher: `py -3.10 run.py --reload`)*

### Option B: Using `uvicorn` Directly
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

When started, you will see:
```text
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     AIS stream connected and subscribed
INFO:     Application startup complete.
```

---

## Interactive Documentation & Testing

Once running, visit:
- **Swagger UI (Interactive API Explorer):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc (Detailed API Specs):** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check JSON:** [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

## API Endpoints Catalog

All endpoints are centrally registered in `app/endpoints.py` under the `/api` prefix:

| Endpoint | Method | Description | Third-Party Source |
| :--- | :---: | :--- | :--- |
| `/api/health` | `GET` | Health check, uptime, and endpoints registry | Local |
| `/api/flights` | `GET` | Live military aircraft positions, types & jamming hotspots | `adsb.fi` / OpenSky |
| `/api/maritime` | `GET` | Live AIS ship positions, ports, chokepoints & congestion | `aisstream.io` WebSocket |
| `/api/satellites` | `GET` | All active satellites with real-time SGP4 orbital propagation | CelesTrak / SatNOGS |
| `/api/satellites/orbit` | `GET` | Full single-orbit trajectory trace or live telemetry | Local TLE Cache |
| `/api/proxy-tiles` | `GET` | CORS proxy for CartoDB / CartoCDN raster map tiles | `cartocdn.com` |
| `/api/region-dossier` | `GET` | OSINT dossier for coordinates: Reverse geocode, Wikidata & Wikipedia | Photon / Wikidata / Wikipedia |
| `/api/stats` | `GET` | High-level tracker counters (flights, satellites, maritime) | In-memory aggregations |

### Endpoint Details & Query Parameters

#### 1. Satellites Orbit (`/api/satellites/orbit`)
- `id` (required): NORAD Catalog ID (e.g. `25544` for ISS)
- `t` (optional): Anchor timestamp in milliseconds to center the orbit
- `live` (optional): Set to `1` for live telemetry (speed, heading, altitude)
- *Example:* `http://localhost:8000/api/satellites/orbit?id=25544&live=1`

#### 2. Region Dossier (`/api/region-dossier`)
- `lat` (required): Latitude float (e.g. `28.6139`)
- `lng` (required): Longitude float (e.g. `77.2090`)
- *Example:* `http://localhost:8000/api/region-dossier?lat=28.6139&lng=77.2090`

#### 3. Map Tile Proxy (`/api/proxy-tiles`)
- `url` (required): URL-encoded CartoCDN tile URL
- *Example:* `http://localhost:8000/api/proxy-tiles?url=https%3A%2F%2Fa.basemaps.cartocdn.com%2Fdark_all%2F0%2F0%2F0.png`

---

## Connecting the Frontend

The Next.js frontend (`SAT_MAP/`) connects automatically to this backend via:
```env
# In SAT_MAP/.env:
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
```
All frontend requests are managed by the centralized client in `SAT_MAP/src/config/api.ts`.
