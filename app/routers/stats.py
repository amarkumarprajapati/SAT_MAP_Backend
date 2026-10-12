"""
LTmap — Platform Summary Stats Router

Aggregates counts of active tracked entities (flights, satellites, maritime)
directly from in-process caches and services without calling external proxies.
Ported from stats/route.ts.
"""

import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..services import ais_stream, tle_cache
from .maritime import PORTS
from . import flights

logger = logging.getLogger("ltmap.stats")

router = APIRouter(prefix="/api/stats", tags=["stats"])

TTL_SECONDS = 30.0
_snapshot: dict | None = None
_snapshot_time: float = 0


async def _compute_stats() -> dict[str, int]:
    # 1. Flights count
    flight_count = 0
    try:
        if flights._cached_data and time.time() - flights._last_fetch < 120:
            flight_count = len(flights._cached_data.get("military_flights", []))
        else:
            # Quick check without blocking if not fetched yet
            pass
    except Exception:
        pass

    # 2. Satellites count
    sat_count = tle_cache.catalogue_size()

    # 3. Maritime count (ships + ports)
    maritime_count = ais_stream.ship_count() + len(PORTS)

    return {
        "flights": flight_count,
        "sats": sat_count,
        "maritime": maritime_count,
    }


@router.get("")
async def get_stats():
    """
    Platform summary statistics: total active tracked flights, satellites, maritime vessels and ports.
    """
    global _snapshot, _snapshot_time

    now = time.time()
    if _snapshot and now - _snapshot_time < TTL_SECONDS:
        return JSONResponse(
            content=_snapshot,
            headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=60"},
        )

    try:
        stats = await _compute_stats()
        # If flight_count is 0 because flights hasn't been fetched yet, try fetching once
        if stats["flights"] == 0 and (not flights._cached_data or now - flights._last_fetch >= 60):
            try:
                data = await flights._fetch_flights()
                flights._cached_data = data
                flights._last_fetch = time.time()
                stats["flights"] = len(data.get("military_flights", []))
            except Exception as e:
                logger.warning("Could not pre-fetch flights for stats: %s", e)

        _snapshot = {
            "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _snapshot_time = now

        return JSONResponse(
            content=_snapshot,
            headers={"Cache-Control": "public, s-maxage=30, stale-while-revalidate=60"},
        )
    except Exception as exc:
        logger.error("Stats aggregation failed: %s", exc)
        return JSONResponse(
            content={"error": "Failed to compute stats"},
            status_code=500,
        )
