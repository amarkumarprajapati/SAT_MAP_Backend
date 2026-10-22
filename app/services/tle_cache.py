import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .. import database
from ..urls import CELESTRAK_TLE, SATNOGS_TLE

logger = logging.getLogger("ltmap.tle")

# ── CelesTrak URLs ──────────────────────────────────────────────────────────────
CELESTRAK_GROUPS = [
    CELESTRAK_TLE.format(group=group)
    for group in (
        "gps-ops", "glonass-operational", "galileo", "beidou", "weather",
        "resource", "planet", "goes", "argos", "dmc", "spire", "military", "radar",
    )
]

# ── Storage ─────────────────────────────────────────────────────────────────────
_cache_file = Path(settings.CACHE_DIR) / "tle-cache.json"

_satellites: list[dict[str, str]] = []   # [{name, line1, line2}, ...]
_cache_time: float = 0
_CACHE_TTL = 3600  # 1 hour

_lock = asyncio.Lock()


def _parse_tle_text(text: str) -> list[dict[str, str]]:
    """Parse raw 3-line TLE text into [{name, line1, line2}, ...]."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    sats = []
    i = 0
    while i < len(lines) - 1:
        if (not lines[i].startswith("1")
                and i + 2 < len(lines)
                and lines[i + 1].startswith("1")
                and lines[i + 2].startswith("2")):
            name = lines[i].lstrip("0 ").strip()
            sats.append({"name": name, "line1": lines[i + 1], "line2": lines[i + 2]})
            i += 3
        elif lines[i].startswith("1") and i + 1 < len(lines) and lines[i + 1].startswith("2"):
            norad_id = lines[i][2:7].strip()
            sats.append({"name": f"SAT-{norad_id}", "line1": lines[i], "line2": lines[i + 1]})
            i += 2
        else:
            i += 1
    return sats


def _norad_id(line1: str) -> str:
    """Extract NORAD catalogue number from TLE line 1 (columns 3-7)."""
    return line1[2:7].strip()


def _save_to_disk(sats: list[dict[str, str]]) -> None:
    """Persist TLE data to disk for warm restarts."""
    try:
        _cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(_cache_file, "w") as f:
            json.dump({"time": time.time(), "sats": sats}, f)
    except Exception as exc:
        logger.warning("Could not save TLE cache: %s", exc)


def _load_from_disk() -> list[dict[str, str]]:
    """Load TLE data from disk (valid up to 4 hours)."""
    try:
        if not _cache_file.exists():
            return []
        with open(_cache_file) as f:
            data = json.load(f)
        if data.get("sats") and time.time() - data.get("time", 0) < 14400:
            logger.info("TLE cache restored from disk: %d satellites", len(data["sats"]))
            return data["sats"]
    except Exception:
        pass
    return []


async def _fetch_celestrak_group(url: str) -> list[dict[str, str]]:
    """Fetch one CelesTrak constellation group."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "L&T/4.2 (satellite-tracker)"})
            if resp.status_code != 200:
                return []
            text = resp.text
            if "has not updated since" in text or len(text) < 100:
                return []
            return _parse_tle_text(text)
    except Exception:
        return []


# ── Initialise from disk on import ──────────────────────────────────────────────
_disk = _load_from_disk()
if _disk:
    _satellites = _disk
    _cache_time = time.time()


# ── Public API ──────────────────────────────────────────────────────────────────

async def refresh(force: bool = False) -> tuple[list[dict[str, str]], str]:
    """
    Return the current TLE catalogue and a source label.

    Re-fetches from CelesTrak if the cache is stale (>1 hour), empty, or too
    small (<5000). Falls back to SatNOGS if CelesTrak yields too few.
    """
    global _satellites, _cache_time

    now = time.time()
    need_refresh = (
        force
        or len(_satellites) == 0
        or (len(_satellites) < 500 and now - _cache_time > 300)
        or now - _cache_time > _CACHE_TTL
    )

    if not need_refresh and len(_satellites) > 0:
        return _satellites, "memory-cache"

    async with _lock:
        # Double-check after acquiring lock
        if not force and len(_satellites) >= 500 and time.time() - _cache_time <= _CACHE_TTL:
            return _satellites, "memory-cache"

        # Fetch all CelesTrak groups in parallel
        tasks = [_fetch_celestrak_group(url) for url in CELESTRAK_GROUPS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen: set[str] = set()
        merged: list[dict[str, str]] = []

        for result in results:
            if isinstance(result, list):
                for sat in result:
                    nid = _norad_id(sat["line1"])
                    if nid not in seen:
                        seen.add(nid)
                        merged.append(sat)

        # Backfill with cached satellites that failed to fetch this round
        backfilled = 0
        for sat in _satellites:
            nid = _norad_id(sat["line1"])
            if nid not in seen:
                seen.add(nid)
                merged.append(sat)
                backfilled += 1

        source = f"celestrak ({len(merged)} TLEs: {len(merged) - backfilled} new, {backfilled} cached)"

        if len(merged) > 500:
            _satellites = merged
            _cache_time = time.time()
            _save_to_disk(merged)
            await database.save_snapshot("tle_catalog", merged)
        else:
            # SatNOGS fallback
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(SATNOGS_TLE, headers={"Accept": "application/json"})
                    if resp.status_code == 200:
                        data = resp.json()
                        fetched = []
                        seen_names: set[str] = set()
                        for item in data:
                            raw_name = (item.get("tle0") or "").strip()
                            clean_name = raw_name.lstrip("0 ").strip()
                            if clean_name and item.get("tle1") and item.get("tle2") and clean_name not in seen_names:
                                seen_names.add(clean_name)
                                fetched.append({
                                    "name": clean_name,
                                    "line1": item["tle1"].strip(),
                                    "line2": item["tle2"].strip(),
                                })
                        if fetched:
                            _satellites = fetched
                            _cache_time = time.time()
                            source = "satnogs-api"
                            _save_to_disk(fetched)
                            await database.save_snapshot("tle_catalog", fetched)
                            return _satellites, source
            except Exception as exc:
                logger.error("SatNOGS fetch error: %s", exc)

        # Emergency fallback
        if not _satellites:
            iss_l1 = "1 25544U 98067A   24146.40251785  .00015505  00000-0  27885-3 0  9997"
            iss_l2 = "2 25544  51.6402 189.7042 0004381 334.8091 106.8778 15.50091157455243"
            _satellites = [{"name": "ISS (FALLBACK)", "line1": iss_l1, "line2": iss_l2}]
            _cache_time = time.time()
            source = "emergency-fallback"

        return _satellites, source


def get_by_norad(norad_id: str) -> dict[str, str] | None:
    """Look up a TLE by NORAD catalogue number (lock-free read)."""
    target = str(int(norad_id))  # normalise "025544" → "25544"
    for sat in _satellites:
        if _norad_id(sat["line1"]) == target:
            return sat
    return None


def catalogue_size() -> int:
    """Number of TLEs currently cached (lock-free, for stats)."""
    return len(_satellites)


async def restore_database_cache() -> None:
    """Use the last saved TLE catalogue while the upstream feed refreshes."""
    global _satellites, _cache_time
    if _satellites:
        return
    saved = await database.load_snapshot("tle_catalog")
    if isinstance(saved, list) and saved:
        _satellites = saved
        # Mark stale so the first API request attempts a refresh and backfills
        # from this catalogue if the upstream provider is unavailable.
        _cache_time = time.time() - _CACHE_TTL - 1
        logger.info("Restored %d TLE records from PostgreSQL", len(saved))
