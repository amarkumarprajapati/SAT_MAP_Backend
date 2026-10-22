"""
LTmap — Flights Router

Fetches live military flight data from adsb.fi, classifies aircraft by type
and callsign, aggregates GPS jamming hotspots. Ported from flights/route.ts.
"""

import time
import asyncio
import re
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..utils.stealth_fetch import stealth_get
from .. import database
from ..urls import ADSBFI_MILITARY

logger = logging.getLogger("ltmap.flights")

router = APIRouter(prefix="/api", tags=["flights"])

# ── Constants ───────────────────────────────────────────────────────────────────
ADSB_MAX_DIST = 250  # nm

HELI_TYPES = {
    "R22", "R44", "R66", "B06", "B06T", "B204", "B205", "B206", "B212", "B222",
    "B230", "B407", "B412", "B427", "B429", "B430", "B505", "B525",
    "AS32", "AS35", "AS50", "AS55", "AS65",
    "EC20", "EC25", "EC30", "EC35", "EC45", "EC55", "EC75",
    "H125", "H130", "H135", "H145", "H155", "H160", "H175", "H215", "H225",
    "S55", "S58", "S61", "S64", "S70", "S76", "S92",
    "A109", "A119", "A139", "A169", "A189", "AW09",
    "MD52", "MD60", "MDHI", "MD90", "NOTR",
    "B47G", "HUEY", "GAMA", "CABR", "EXE",
}

PRIVATE_JET_TYPES = {
    "G150", "G200", "G280", "GLEX", "G500", "G550", "G600", "G650", "G700",
    "GLF2", "GLF3", "GLF4", "GLF5", "GLF6", "GL5T", "GL7T", "GV", "GIV",
    "CL30", "CL35", "CL60", "BD70", "BD10",
    "C25A", "C25B", "C25C", "C500", "C510", "C525", "C550", "C560", "C56X", "C680", "C700", "C750",
    "E35L", "E50P", "E55P", "E545", "E550",
    "FA50", "FA7X", "FA8X", "F900", "F2TH",
    "LJ35", "LJ40", "LJ45", "LJ60", "LJ70", "LJ75",
    "PC12", "PC24", "TBM7", "TBM8", "TBM9",
    "PRM1", "SF50", "EA50", "VLJ",
}

MILITARY_INDICATORS = {
    "C17", "C5M", "C130", "C30J", "KC10", "KC46", "KC35", "E3CF", "E3TF", "E8A",
    "B1B", "B2", "B52", "F16", "F15", "F18", "F22", "F35", "A10", "F117",
    "RC135", "E6B", "P8A", "P3", "MQ9", "RQ4", "U2", "EP3", "RC12",
    "V22", "CH47", "UH60", "AH64", "AH1Z", "MV22",
    "EUFI", "RFAL", "TORD", "TYP", "GR4",
}

AIRLINER_TYPES = {
    "A319", "A320", "A321", "A332", "A333", "A339", "A343", "A359", "A388",
    "B737", "B738", "B739", "B38M", "B39M", "B752", "B753", "B763", "B764",
    "B772", "B77L", "B77W", "B788", "B789", "B78X",
    "E170", "E175", "E190", "E195", "CRJ7", "CRJ9", "AT43", "AT72", "DH8D",
}

BIZJET_OPERATORS = {
    "EJA", "EJM", "NJE", "LXJ", "FJO", "VJT", "XOJ", "JTL", "WUP", "GAJ", "DPJ", "CLY", "TWY",
}

AIRLINE_CODE_RE = re.compile(r"^([A-Z]{3})\d")
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,8}$")
MIL_CALLSIGN_RE = re.compile(r"^(RCH|KING|DUKE|EVAC|JAKE|REACH|CONVOY)\d", re.IGNORECASE)

JET_CRUISE_ALT_M = 8500
JET_CRUISE_KTS = 300
JAMMING_NACAP_THRESHOLD = 4

# ── Cache ───────────────────────────────────────────────────────────────────────
_cached_data: dict | None = None
_last_fetch: float = 0
_CACHE_TTL = 90  # seconds
_fetch_lock = asyncio.Lock()


def _classify_flight(f: dict) -> dict | None:
    """Classify a raw ADS-B record into a flight category. Returns None for filtered-out flights."""
    model_upper = (f.get("t") or "").upper()
    flight_str = (f.get("flight") or "").strip().upper()
    db_flags = f.get("dbFlags", 0) or 0

    if model_upper == "TWR":
        return None

    lat = f.get("lat")
    lon = f.get("lon")
    if lat is None or lon is None:
        return None

    callsign = flight_str or f.get("hex", "UNKNOWN")
    alt_raw = f.get("alt_baro")
    alt_meters = (alt_raw * 0.3048) if isinstance(alt_raw, (int, float)) else 0
    speed_knots = round(f["gs"] * 10) / 10 if isinstance(f.get("gs"), (int, float)) else None
    heading = f.get("track") or 0
    is_heli = model_upper in HELI_TYPES or f.get("category_os") == 8
    is_grounded = isinstance(alt_raw, (int, float)) and alt_raw < 100

    is_os_military = f.get("category_os") == 14
    is_os_high_perf = f.get("category_os") == 7
    is_os_light = f.get("category_os") == 2
    is_os_heavy = f.get("category_os") in (4, 5, 6)

    airline_match = AIRLINE_CODE_RE.match(callsign)
    airline_code = airline_match.group(1) if airline_match else ""

    is_ga_callsign = not airline_code and bool(CALLSIGN_RE.match(flight_str))
    cruises_like_jet = alt_meters > JET_CRUISE_ALT_M and (speed_knots or 0) > JET_CRUISE_KTS

    # Classification
    category = "commercial"
    if (is_os_military or (db_flags & 1)
            or model_upper in MILITARY_INDICATORS
            or MIL_CALLSIGN_RE.match(flight_str or "")):
        category = "military"
    elif model_upper in AIRLINER_TYPES or is_os_heavy:
        category = "commercial"
    elif (airline_code in BIZJET_OPERATORS
          or model_upper in PRIVATE_JET_TYPES
          or is_os_high_perf
          or (is_ga_callsign and cruises_like_jet)):
        category = "jet"
    elif is_ga_callsign or is_os_light:
        category = "private"

    # Only keep military flights (matching frontend behaviour)
    if category != "military":
        return None

    return {
        "callsign": callsign,
        "lat": round(lat, 5),
        "lng": round(lon, 5),
        "alt": round(alt_meters),
        "heading": round(heading),
        "speed_knots": speed_knots,
        "model": f.get("t") or "Unknown",
        "icao24": f.get("hex", ""),
        "registration": f.get("r") or "N/A",
        "squawk": f.get("squawk") or "",
        "airline_code": airline_code,
        "aircraft_category": "heli" if is_heli else "plane",
        "category": category,
        "grounded": is_grounded,
        "nac_p": f.get("nac_p"),
        "type": "flight",
    }


def _aggregate_jamming(points: list[dict]) -> list[dict]:
    """Aggregate GPS jamming indicators into a grid."""
    if not points:
        return []

    GRID_SIZE = 2
    grid: dict[str, dict] = {}

    for p in points:
        g_lat = (p["lat"] // GRID_SIZE) * GRID_SIZE
        g_lng = (p["lng"] // GRID_SIZE) * GRID_SIZE
        key = f"{g_lat},{g_lng}"
        if key not in grid:
            grid[key] = {"lat": g_lat + GRID_SIZE / 2, "lng": g_lng + GRID_SIZE / 2, "count": 0, "total_nac_p": 0}
        cell = grid[key]
        cell["count"] += 1
        cell["total_nac_p"] += p["nac_p"]

    return [
        {
            "lat": z["lat"],
            "lng": z["lng"],
            "severity": round((1 - (z["total_nac_p"] / z["count"]) / JAMMING_NACAP_THRESHOLD) * 100),
            "count": z["count"],
        }
        for z in grid.values()
        if z["count"] >= 3
    ]


async def _fetch_flights() -> dict:
    """Fetch and classify military flights from adsb.fi."""
    all_raw: list[dict] = []
    seen_hex: set[str] = set()

    # Fetch adsb.fi military feed
    try:
        resp = await stealth_get(ADSBFI_MILITARY, timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            for ac in data.get("ac", []):
                hex_id = (ac.get("hex") or "").lower().strip()
                if hex_id and hex_id not in seen_hex:
                    seen_hex.add(hex_id)
                    all_raw.append(ac)
    except Exception as exc:
        logger.warning("adsb.fi mil fetch error: %s", exc)

    mil_count = len(all_raw)

    # Classify
    military = []
    gps_jamming = []

    for raw in all_raw:
        flight = _classify_flight(raw)
        if not flight:
            continue

        if (isinstance(flight.get("nac_p"), (int, float))
                and flight["nac_p"] <= JAMMING_NACAP_THRESHOLD
                and not flight["grounded"]):
            gps_jamming.append({
                "lat": flight["lat"],
                "lng": flight["lng"],
                "nac_p": flight["nac_p"],
                "callsign": flight["callsign"],
            })

        military.append(flight)

    return {
        "commercial_flights": [],
        "private_flights": [],
        "private_jets": [],
        "military_flights": military,
        "gps_jamming": _aggregate_jamming(gps_jamming),
        "total": len(all_raw),
        "source": "adsbfi-mil",
        "providers": {"adsbfi_mil": mil_count},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/flights")
async def get_flights():
    """Fetch live global military flight tracking data from adsb.fi."""
    global _cached_data, _last_fetch

    now = time.time()
    if _cached_data and now - _last_fetch < _CACHE_TTL:
        return JSONResponse(
            content=_cached_data,
            headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=60"},
        )

    async with _fetch_lock:
        # Double-check after lock
        if _cached_data and time.time() - _last_fetch < _CACHE_TTL:
            return JSONResponse(
                content=_cached_data,
                headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=60"},
            )

        try:
            data = await _fetch_flights()
            _cached_data = data
            _last_fetch = time.time()
            await database.save_snapshot("flights", data)

            cache_ctl = (
                "no-store, max-age=0"
                if data["total"] < 100
                else "public, s-maxage=30, stale-while-revalidate=60"
            )
            return JSONResponse(content=data, headers={"Cache-Control": cache_ctl})

        except Exception as exc:
            logger.error("Flight fetch error: %s", exc)
            if not _cached_data:
                _cached_data = await database.load_snapshot("flights")
            if _cached_data:
                stale = {**_cached_data, "source": (_cached_data.get("source", "unknown") + "+stale")}
                return JSONResponse(content=stale, headers={"Cache-Control": "no-store, max-age=0"})
            return JSONResponse(content={"error": "Failed to fetch flight data"}, status_code=500)
