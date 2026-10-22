# LTmap FastAPI Backend

FastAPI backend powering the LTmap geospatial and OSINT dashboard. It combines live data feeds, SGP4 satellite propagation, AIS streams, and a small PostgreSQL store for durable data snapshots.

The browser calls FastAPI only. FastAPI reads OSINT news from the NestJS service at `OSINT_SERVICE_URL` and merges it into `/api/news`.

---

## Quick Start

### 1. Prerequisites
- **Python 3.10+**
- `pip` package manager
- Docker Compose for the production-style PostgreSQL setup

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
Use `.env.example` as a template. Add the PostgreSQL settings to your local `.env` while preserving any existing API keys, then set the database password and public frontend origin:
```env
APP_ENV=production
HOST=0.0.0.0
PORT=8000
BASE_URL=https://api.example.com
API_PREFIX=/api
CORS_ORIGINS=https://your-frontend.example.com

# PostgreSQL (Compose passes the password separately, so punctuation is supported)
POSTGRES_DB=sat_map
POSTGRES_USER=sat_map
POSTGRES_PASSWORD=replace-with-a-long-random-password
DATABASE_REQUIRED=true
```
Keep `.env` private. Docker Compose starts PostgreSQL, waits for its health check, and then starts the API. The database stores the latest TLE catalogue, flight feed, and news snapshots in a JSONB table. The named `postgres_data` volume keeps them across container restarts.

---

## Running the Server

### Option A: Using Docker Compose (Recommended)
Make sure Docker Desktop is installed and running.

```bash
# 1. Configure .env as described above, then build and start the API and PostgreSQL
docker compose up -d --build

# 2. View real-time logs
docker compose logs -f

# 3. Check health and container status
docker compose ps

# 4. Stop the container
docker compose down
```

### Option B: Using Standalone Docker
Use standalone Docker only when you provide a `DATABASE_URL` that points to a reachable PostgreSQL server. Compose starts and configures PostgreSQL for you.

```bash
# Build the image
docker build -t sat-map-backend .

# Run the container
docker run -d --name sat_map_backend -p 8000:8000 --env-file .env sat-map-backend

# Stop and remove
docker stop sat_map_backend && docker rm sat_map_backend
```

### Option C: Using `run.py` (Local Python)
```powershell
# Development mode (PostgreSQL is optional; set DATABASE_URL to enable it)
$env:APP_ENV = "development"
$env:DATABASE_REQUIRED = "false"
python run.py

# With hot-reload for active development
python run.py --reload
```
*(If using the Windows Python launcher: `py -3.10 run.py --reload`)*

### Option D: Using `uvicorn` Directly
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

In production, `APP_ENV=production` requires `DATABASE_URL`, `DATABASE_REQUIRED=true`, and explicit non-wildcard `CORS_ORIGINS`. The API health endpoint reports PostgreSQL status and returns HTTP 503 if the required database is unavailable. Interactive API docs are disabled in production.

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

## Third-Party Data Sources & External APIs

For the exhaustive technical specification with field-level schemas, see **[DATA_SOURCES.md](DATA_SOURCES.md)**.

| Domain | 3rd-Party Provider | Protocol | What Data is Received & Processed |
| :--- | :--- | :--- | :--- |
| **Maritime (Global)** | **AISStream** (`aisstream.io`) | WebSocket (`wss://`) | **Live AIS vessel tracking:** MMSI, vessel name, coordinates, speed (SOG), heading (COG/TrueHeading), vessel type (cargo, tanker, military), destination. Backend calculates dynamic port congestion & chokepoint transit volumes. |
| **Maritime (Baltic / Nordic)** | **Fintraffic Digitraffic** (`digitraffic.fi`) | MQTT over TLS | **Public Finnish/Baltic AIS feed:** High-frequency location updates and vessel metadata, merged into the main in-memory ship registry without API keys. |
| **Military Flights & GPS Jamming** | **adsb.fi** | HTTPS REST | **Global airborne transponders:** ICAO24 hex, callsigns, airframe models, barometric altitude, ground speed, track heading. Backend classifies military aircraft & detects GPS jamming/spoofing hotspots (`nac_p <= 4`). |
| **Satellite Orbits (Primary)** | **CelesTrak** (`celestrak.org`) | HTTPS REST | **Two-Line Elements (TLEs):** 13 constellation groups (GPS, GLONASS, Galileo, BeiDou, Weather, Earth Resources, Planet Labs, Military Recon, Radar). Propagated locally using SGP4/SDP4 Keplerian orbital mechanics. |
| **Satellite Orbits (Fallback)** | **SatNOGS DB** (`satnogs.org`) | HTTPS REST | **Crowdsourced satellite TLE database:** Automated failover source if CelesTrak is unavailable or rate-limited. |
| **Reverse Geocoding** | **Photon by Komoot** (OpenStreetMap) | HTTPS REST | **Coordinate-to-location mapping:** Converts `lat, lng` to city, state, country, and ISO country code. |
| **Country Intelligence** | **Wikidata REST & Action APIs** | HTTPS REST | **Geopolitical facts:** Capital city, continent, official languages, currencies, head of state, national flag image URL, population, and land area. |
| **Regional Summary** | **Wikipedia REST API** | HTTPS REST | **Geographical overview:** Article synopsis (500 chars) and thumbnail image for the target region or capital. |

---

## Connecting the Frontend

The Next.js frontend (`SAT_MAP/`) connects automatically to this backend via:
```env
# In SAT_MAP/.env:
NEXT_PUBLIC_FASTAPI_URL=http://localhost:8000
```
All frontend requests are managed by the centralized client in `SAT_MAP/src/config/api.ts`.
