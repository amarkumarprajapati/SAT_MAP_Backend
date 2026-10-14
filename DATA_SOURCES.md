# Third-Party Data Sources & External APIs Specification

This document provides a comprehensive technical reference for all external/third-party data providers, APIs, streaming protocols, data schemas, update frequencies, and caching mechanisms utilized by the **LTmap FastAPI Backend**.

---

## High-Level Summary Matrix

| Data Domain | External Provider / Service | Protocol | Endpoint / URL | Auth / Key Required | Update Cadence / Cache |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Global Maritime AIS** | **AISStream** (`aisstream.io`) | WebSocket (WSS) | `wss://stream.aisstream.io/v0/stream` | `AIS_API_KEY` (Required) | Real-time push / in-memory buffer (20k vessels) |
| **Regional Maritime AIS** | **Fintraffic Digitraffic** (`digitraffic.fi`) | MQTT over WSS (TLS) | `wss://meri.digitraffic.fi:443/mqtt` | None (Free open data) | Real-time push / merged with AISStream |
| **Military Aviation & Jamming** | **adsb.fi** | HTTPS REST (GET) | `https://opendata.adsb.fi/api/v2/mil` | None (Public open data) | 90s server-side memory cache |
| **Satellite TLE (Primary)** | **CelesTrak** (`celestrak.org`) | HTTPS REST (GET) | `https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle` | None | 1-hour memory TTL / 4-hour disk cache |
| **Satellite TLE (Fallback)** | **SatNOGS DB** (`satnogs.org`) | HTTPS REST (GET) | `https://db.satnogs.org/api/tle/?format=json` | None | On-demand fallback when CelesTrak fails |
| **Reverse Geocoding** | **Photon by Komoot** (OSM) | HTTPS REST (GET) | `https://photon.komoot.io/reverse` | None | 24-hour memory cache |
| **Country QID Resolution** | **Wikipedia MediaWiki API** | HTTPS REST (GET) | `https://en.wikipedia.org/w/api.php` | None | 24-hour memory cache |
| **Country Facts & Heads of State** | **Wikidata REST & Action APIs** | HTTPS REST (GET) | `https://www.wikidata.org/w/rest.php/wikibase/v1/...` | None | 24-hour memory cache |
| **Country / City Synopses** | **Wikipedia REST API** | HTTPS REST (GET) | `https://en.wikipedia.org/api/rest_v1/page/summary/{title}` | None | 24-hour memory cache |

---

## 1. Maritime Intelligence & AIS Tracking

The backend combines two real-time AIS (Automatic Identification System) feeds to monitor global cargo, tanker, and naval movements.

### 1.1 AISStream (`aisstream.io`) — Global Ocean Coverage
- **Source Service:** AISStream Live Maritime Stream
- **Transport / Protocol:** Persistent WebSocket (`wss://stream.aisstream.io/v0/stream`)
- **Backend Service:** [`app/services/ais_stream.py`](app/services/ais_stream.py)
- **Authentication:** Configured via `AIS_API_KEY` in `.env`
- **Subscription Payload:**
  ```json
  {
    "APIKey": "<AIS_API_KEY>",
    "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
    "FilterMessageTypes": ["PositionReport", "ShipStaticData"]
  }
  ```
- **Incoming Data Fields:**
  - `PositionReport`:
    - `MetaData.MMSI`: Unique 9-digit Maritime Mobile Service Identity
    - `MetaData.ShipName`: Registered vessel name
    - `MetaData.latitude` / `MetaData.longitude`: Instantaneous coordinates
    - `Message.PositionReport.Latitude` & `Longitude`: GPS fix
    - `Message.PositionReport.Sog`: Speed Over Ground (knots)
    - `Message.PositionReport.Cog`: Course Over Ground (degrees)
    - `Message.PositionReport.TrueHeading`: Ship compass heading (0–359°)
  - `ShipStaticData`:
    - `Message.ShipStaticData.Name`: Vessel name
    - `Message.ShipStaticData.Destination`: Declared destination port
    - `Message.ShipStaticData.Type`: Numerical AIS vessel category code:
      - `70..79` & default $\rightarrow$ `cargo`
      - `80..89` $\rightarrow$ `tanker`
      - `35, 50, 51, 55` $\rightarrow$ `military`
- **Internal Processing & Features:**
  - Auto-reconnecting background daemon with heartbeats (ping interval: 30s).
  - In-memory vessel hash table (`_ships`, up to 20,000 ships) with LRU eviction.
  - **Dynamic Port Congestion Analysis:** Calculates waiting vessels (speed < 0.5 knots within 50 km of 40+ major global ports like Shanghai, Singapore, Rotterdam, Fujairah, etc.) to estimate port dwell times (`NORMAL`, `CONGESTED`, `SEVERE`).
  - **Strategic Chokepoint Risk:** Evaluates live traffic counts across 10 critical bottlenecks (Strait of Hormuz, Strait of Malacca, Suez Canal, Bab el-Mandeb, Taiwan Strait, Panama Canal).
  - **Targeted Streaming:** Broadcasts updates over WebSocket (`/api/maritime/stream?mmsi={mmsi}`) so clients only stream the vessels they are actively inspecting.

### 1.2 Fintraffic Digitraffic — Baltic & Northern Europe Supplement
- **Source Service:** Fintraffic Marine API (Finnish Transport Infrastructure Agency)
- **Transport / Protocol:** MQTT v3.1.1 over WebSockets with TLS (`wss://meri.digitraffic.fi:443/mqtt`)
- **Backend Service:** [`app/services/digitraffic_ais.py`](app/services/digitraffic_ais.py)
- **Authentication:** None (Public Open Data)
- **Subscribed MQTT Topics:** `vessels-v2/#`
  - `vessels-v2/{mmsi}/locations`: Real-time position fixes (`lat`, `lon`, `sog`, `heading`, `cog`, `time`)
  - `vessels-v2/{mmsi}/metadata`: Vessel specifics (`name`, `destination`, `type`)
- **Internal Processing:** Automatically ingested and merged into the shared vessel dictionary via `ais_stream.ingest_external_ship()`.

---

## 2. Aerial Reconnaissance & Military Flights

### adsb.fi OpenData API
- **Source Service:** adsb.fi Community ADS-B Exchange
- **Transport / Protocol:** HTTPS GET
- **Backend Router:** [`app/routers/flights.py`](app/routers/flights.py)
- **Endpoint URL:** `https://opendata.adsb.fi/api/v2/mil`
- **Authentication:** None (Open Data)
- **Request Mechanism:** Executed via stealth HTTP client (`app/utils/stealth_fetch.py`) with rotated modern browser headers to avoid CDN rate-limiting.
- **Cache Strategy:** 90-second in-memory server cache with `stale-while-revalidate` support.
- **Incoming Data Fields:**
  - `hex`: 24-bit ICAO aircraft address (hexadecimal)
  - `flight`: Callsign transmitted by transponder
  - `r`: Aircraft registration / tail number
  - `t`: ICAO aircraft type designator (e.g., `C17`, `P8`, `RC135`, `F35`, `EUFI`, `B52`)
  - `alt_baro`: Barometric altitude in feet
  - `gs`: Ground speed in knots
  - `track`: True track / heading in degrees
  - `lat` / `lon`: Aircraft position
  - `squawk`: Transponder 4-digit beacon code
  - `nac_p`: Navigation Accuracy Category for Position (0 to 11)
  - `dbFlags`: Database flags (bit 0 = military)
  - `category_os`: OpenSky category indicator
- **Internal Processing & Derived Intelligence:**
  1. **Military Aircraft Filtering & Classification:**
     - Checks callsign prefixes (`RCH`, `KING`, `DUKE`, `EVAC`, `REACH`, `CONVOY`, etc.)
     - Checks airframe model lists (Transport, Tanker, AWACS, Reconnaissance, Fighters, Bombers, Attack Helicopters)
     - Maps aircraft categories into `heli` or `plane`.
  2. **GPS Jamming & Spoofing Grid:**
     - Inspects `nac_p` (Navigational Accuracy Category). Values $\le 4$ while cruising above ground indicate degraded satellite positioning (GPS jamming / spoofing).
     - Aggregates degraded coordinates onto a global 2° $\times$ 2° latitude/longitude spatial grid.
     - Computes a percentage `severity` score for active conflict zones (e.g. Eastern Mediterranean, Black Sea, Baltic Sea).
  - Exposed via `/api/flights`.

---

## 3. Satellite Tracking & Orbital Mechanics (TLE / SGP4)

### 3.1 CelesTrak (`celestrak.org`) — Primary TLE Source
- **Source Service:** CelesTrak NORAD GP Elements
- **Transport / Protocol:** HTTPS GET
- **Backend Service:** [`app/services/tle_cache.py`](app/services/tle_cache.py)
- **Base Endpoint:** `https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle`
- **Authentication:** None
- **Constellation Groups Queried in Parallel:**
  - `gps-ops`: US Space Force GPS Operational Constellation
  - `glonass-operational`: Russian GLONASS Navigation
  - `galileo`: European Union Galileo Navigation
  - `beidou`: Chinese BeiDou Navigation
  - `weather`: Meteorological Satellites
  - `resource`: Earth Resource / Landsat / Sentinel
  - `planet`: Planet Labs SkySat & Dove Earth-imaging CubeSats
  - `goes`: NOAA Geostationary Operational Environmental Satellites
  - `argos`: Argos Environmental Data Relays
  - `dmc`: Disaster Monitoring Constellation
  - `spire`: Spire Global Maritime & Weather Sensors
  - `military`: Classified & Defense Reconnaissance Satellites
  - `radar`: Synthetic Aperture Radar (SAR) Satellites
- **Cache Strategy:**
  - **In-Memory TTL:** 1 Hour (`_CACHE_TTL = 3600s`)
  - **Disk Persistence:** Serialized to `.cache/tle-cache.json` (valid for up to 4 hours across cold server restarts)
  - **Background Worker:** Warm background refresh initiated on application startup.

### 3.2 SatNOGS DB (`db.satnogs.org`) — Automated Fallback
- **Source Service:** Libre Space Foundation SatNOGS Satellite Database
- **Endpoint URL:** `https://db.satnogs.org/api/tle/?format=json`
- **Fallback Trigger:** If CelesTrak returns $< 500$ orbital elements due to upstream outages or network blocks.

### 3.3 Internal Propagation Engine
- **Backend Utility:** [`app/utils/orbit.py`](app/utils/orbit.py)
- **Algorithms:** Mathematical SGP4 / SDP4 Keplerian orbital propagation
- **Calculations Provided:**
  - Instantaneous latitude, longitude, and altitude (km)
  - Orbital velocity vector (km/s) and sub-satellite footprint coverage radius
  - Full single-revolution trajectory path forward & backward in time (`/api/satellites/orbit?id={norad_id}`)
  - Antimeridian discontinuity geometry splitting (crossing $\pm 180^\circ$ longitude without map rendering glitches).

---

## 4. Geopolitical OSINT & Region Dossiers

When a user clicks or inspects any global coordinate, the backend aggregates data from three open-source knowledge bases into a unified dossier (`/api/region-dossier?lat={lat}&lng={lng}`).

### 4.1 Photon by Komoot (OpenStreetMap Geocoding)
- **Source Service:** Photon Geocoding Engine
- **Endpoint URL:** `https://photon.komoot.io/reverse?lat={lat}&lon={lon}&lang=en&radius=50`
- **Data Extracted:**
  - `city`: Nearest municipality / city
  - `state`: Province, district, or state
  - `country`: Full sovereign country name
  - `countrycode`: 2-letter ISO 3166-1 alpha-2 code
- **Cache:** 24-hour memory cache keyed by coordinate rounded to 3 decimal places.

### 4.2 Wikipedia MediaWiki API (Entity ID Resolver)
- **Endpoint URL:** `https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&format=json&titles={country_name}`
- **Purpose:** Resolves the sovereign country title to its canonical Wikidata Q-identifier (e.g. `Q668` for India, `Q30` for USA).

### 4.3 Wikidata REST API & Action API (Country Intelligence)
- **Endpoints:**
  - Statements: `https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{qid}/statements`
  - Entity Labels: `https://www.wikidata.org/w/api.php?action=wbgetentities&props=labels&languages=en&format=json&ids={ids}`
  - Flag Asset: `https://commons.wikimedia.org/wiki/Special:FilePath/{filename}`
- **Claims & Properties Extracted:**
  - `P36`: Capital City
  - `P30`: Continent / Region
  - `P37`: Official Languages (first 4 active)
  - `P38`: Currencies (first 3 active)
  - `P35`: Head of State (e.g., President, Monarch)
  - `P1906`: Head of Government / Executive Office Title
  - `P41`: National Flag SVG/PNG image on Wikimedia Commons
  - `P1082`: Total Population
  - `P2046`: Total Geographic Land Area (sq km)
- **Cache:** 24-hour memory cache per country.

### 4.4 Wikipedia REST API (Regional Synopsis)
- **Endpoint URL:** `https://en.wikipedia.org/api/rest_v1/page/summary/{city_or_country}`
- **Data Extracted:**
  - Article title
  - First 500 characters of the summary text
  - Representative thumbnail image URL.

---

## 5. Environment Variables & Credentials Reference

The following environment variables configure external integrations in `backend/.env`:

```env
# AISStream (Live Global Maritime Ship Tracking)
# Get a free key at: https://aisstream.io
AIS_API_KEY=5deb54fff0481c85565cc4ddc9883875500ffc6e

# OpenSky Network (Optional, for higher rate limits on fallback flight queries)
OPENSKY_CLIENT_ID=
OPENSKY_CLIENT_SECRET=

# Network & Timeout Configuration
TIMEOUT_SECONDS=30.0
HOST=0.0.0.0
PORT=8000
BASE_URL=http://localhost:8000
API_PREFIX=/api
CACHE_DIR=./.cache
```
