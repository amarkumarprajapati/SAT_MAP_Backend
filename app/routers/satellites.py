"""
LTmap — Satellites & Orbit Router

Provides:
- GET /api/satellites — Real-time positions for all active classified satellites
  (military, navigation, Earth observation), computed from SGP4/SDP4 propagation.
- GET /api/satellites/orbit — Single-satellite orbit trace (full revolution) or live
  telemetry, computed on demand from cached TLEs.
Ported from satellites/route.ts and satellites/orbit/route.ts.
"""

import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from ..services import tle_cache
from ..utils.orbit import (
    propagate_tle,
    propagate_telemetry,
    orbit_path,
    orbital_period_minutes,
    split_at_antimeridian,
)

logger = logging.getLogger("ltmap.satellites")

router = APIRouter(prefix="/api/satellites", tags=["satellites"])

# ── Mission classification by NORAD name keywords ──────────────────────────────
MISSION_CLASSIFY: dict[str, dict[str, str]] = {
    "USA": {"mission": "Military Recon", "color": "#FF3D3D"},
    "NROL": {"mission": "NRO Classified", "color": "#FF3D3D"},
    "LACROSSE": {"mission": "SAR Imaging", "color": "#00E5FF"},
    "MENTOR": {"mission": "SIGINT", "color": "#FFFFFF"},
    "ORION": {"mission": "SIGINT", "color": "#FFFFFF"},
    "TRUMPET": {"mission": "SIGINT", "color": "#FFFFFF"},
    "GPS": {"mission": "Navigation", "color": "#448AFF"},
    "NAVSTAR": {"mission": "Navigation", "color": "#448AFF"},
    "GLONASS": {"mission": "Navigation", "color": "#448AFF"},
    "GALILEO": {"mission": "Navigation", "color": "#448AFF"},
    "BEIDOU": {"mission": "Navigation", "color": "#448AFF"},
    "SBIRS": {"mission": "Early Warning", "color": "#FF00FF"},
    "DSP": {"mission": "Early Warning", "color": "#FF00FF"},
    "STARLINK": {"mission": "Commercial Comms", "color": "#00E676"},
    "ONEWEB": {"mission": "Commercial Comms", "color": "#00E676"},
    "PLANET": {"mission": "Earth Imaging", "color": "#00E676"},
    "WORLDVIEW": {"mission": "Commercial Imaging", "color": "#00E676"},
    "ISS": {"mission": "Space Station", "color": "#FFD700"},
    "TIANGONG": {"mission": "Space Station", "color": "#FFD700"},
    "COSMOS": {"mission": "Russian Military", "color": "#FF6B6B"},
    "YAOGAN": {"mission": "Chinese Recon", "color": "#FF6B6B"},
    "FENGYUN": {"mission": "Weather", "color": "#87CEEB"},
    "GOES": {"mission": "Weather", "color": "#87CEEB"},
    "NOAA": {"mission": "Weather", "color": "#87CEEB"},
    "METEOSAT": {"mission": "Weather", "color": "#87CEEB"},
    "LANDSAT": {"mission": "Earth Observation", "color": "#90EE90"},
    "SENTINEL": {"mission": "Earth Observation", "color": "#90EE90"},
    "TERRA": {"mission": "Earth Science", "color": "#90EE90"},
    "AQUA": {"mission": "Earth Science", "color": "#90EE90"},
    "HUBBLE": {"mission": "Space Telescope", "color": "#FFD700"},
    "JAMES WEBB": {"mission": "Space Telescope", "color": "#FFD700"},
}

ALLOWED_CATEGORIES = {"military", "navigation", "earth_obs"}


def _classify_satellite(name: str) -> dict[str, str]:
    upper = name.upper()
    for keyword, info in MISSION_CLASSIFY.items():
        if keyword in upper:
            return info
    return {"mission": "Unknown", "color": "#00E5FF"}


def _get_category(name: str, mission: str) -> str:
    upper = name.upper()
    if " DEB" in upper or "DEBRIS" in upper or " R/B" in upper:
        return "other"
    if mission in ("Commercial Comms", "Commercial Imaging"):
        return "comms"
    if mission == "Navigation":
        return "navigation"
    if mission in ("Weather", "Earth Observation", "Earth Science"):
        return "earth_obs"
    if mission in (
        "Military Recon", "NRO Classified", "SIGINT", "Early Warning",
        "Russian Military", "Chinese Recon", "SAR Imaging",
    ):
        return "military"
    if mission in ("Space Station", "Space Telescope"):
        return "science"
    return "other"


# ── Satellites Endpoint ─────────────────────────────────────────────────────────

@router.get("")
async def get_satellites(_cb: Optional[int] = Query(None)):
    """
    Fetch all active satellites with positions, categorized missions, and orbits.
    Propagated in real-time via SGP4/SDP4.
    """
    try:
        all_sats, source = await tle_cache.refresh()

        now = datetime.now(timezone.utc)
        satellites = []

        for sat in all_sats:
            pos = propagate_tle(sat["line1"], sat["line2"], now)
            if not pos:
                continue

            info = _classify_satellite(sat["name"])
            category = _get_category(sat["name"], info["mission"])

            if category not in ALLOWED_CATEGORIES:
                continue

            norad_id = sat["line1"][2:7].strip()
            satellites.append({
                "name": sat["name"],
                "lat": pos["lat"],
                "lng": pos["lng"],
                "alt": pos["alt_km"],
                "mission": info["mission"],
                "color": info["color"],
                "category": category,
                "noradId": norad_id,
                "line1": sat["line1"],
                "line2": sat["line2"],
            })

        category_counts: dict[str, int] = {}
        for s in satellites:
            cat = s["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1

        cache_ctl = (
            "no-store, max-age=0"
            if len(satellites) < 10
            else "public, s-maxage=120, stale-while-revalidate=300"
        )

        return JSONResponse(
            content={
                "satellites": satellites,
                "total": len(satellites),
                "category_counts": category_counts,
                "source": source,
                "raw_count": len(all_sats),
                "timestamp": now.isoformat(),
            },
            headers={"Cache-Control": cache_ctl},
        )
    except Exception as exc:
        logger.error("Satellite fetch error: %s", exc)
        return JSONResponse(
            content={"satellites": [], "error": "Failed to fetch satellite data"},
            status_code=500,
        )


# ── Orbit Endpoint ──────────────────────────────────────────────────────────────

@router.get("/orbit")
async def get_satellite_orbit(
    id: str = Query(..., description="NORAD Catalog Number"),
    t: Optional[int] = Query(None, description="Timestamp ms for calculation"),
    live: Optional[int] = Query(None, description="Request live point (1)"),
):
    """
    Retrieve orbit trace coordinates or live position for a specific satellite.
    """
    norad_str = id.strip()
    if not norad_str.isdigit():
        return JSONResponse(
            content={"error": "numeric NORAD id required"},
            status_code=400,
        )

    tle = tle_cache.get_by_norad(norad_str)
    if not tle:
        return JSONResponse(
            content={"error": "not in catalogue", "noradId": norad_str},
            status_code=404,
        )

    period_minutes = orbital_period_minutes(tle["line2"])

    if live == 1:
        telemetry = propagate_telemetry(tle["line1"], tle["line2"])
        if not telemetry:
            return JSONResponse(
                content={"error": "could not propagate", "noradId": norad_str},
                status_code=422,
            )
        return JSONResponse(
            content={
                "noradId": norad_str,
                "periodMinutes": period_minutes,
                "position": telemetry,
            },
            headers={"Cache-Control": "no-store"},
        )

    # Calculate anchor time
    now_utc = datetime.now(timezone.utc)
    anchor = now_utc
    if t is not None and t > 0:
        try:
            cand = datetime.fromtimestamp(t / 1000.0, tz=timezone.utc)
            # Drift check: reject if > 7 days away from now
            if abs((cand - now_utc).total_seconds()) <= 7 * 86400:
                anchor = cand
        except Exception:
            anchor = now_utc

    from_dt = (
        anchor - timedelta(minutes=period_minutes / 2.0)
        if period_minutes
        else anchor
    )

    path = orbit_path(tle["line1"], tle["line2"], steps=180, from_dt=from_dt)
    if len(path) < 2:
        return JSONResponse(
            content={"error": "could not propagate", "noradId": norad_str},
            status_code=422,
        )

    runs = split_at_antimeridian(path)
    segments = [
        [
            [round(p["lng"], 4), round(p["lat"], 4), round(p["alt_km"])]
            for p in run
        ]
        for run in runs
    ]

    return JSONResponse(
        content={
            "noradId": norad_str,
            "name": tle["name"],
            "periodMinutes": period_minutes,
            "anchoredAt": anchor.isoformat(),
            "segments": segments,
        },
        headers={
            "Cache-Control": "public, s-maxage=600, stale-while-revalidate=3600"
        },
    )
