"""
LTmap — Region Dossier Router

Provides country intelligence for any coordinate: reverse geocoding via
Photon (komoot), country facts from Wikidata REST API, and Wikipedia summary.
Ported from region-dossier/route.ts.
"""

import asyncio
import logging
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from ..urls import (
    PHOTON_REVERSE,
    WIKIDATA_API,
    WIKIDATA_ITEMS,
    WIKIMEDIA_FILE,
    WIKIPEDIA_API,
    WIKIPEDIA_SUMMARY,
)

logger = logging.getLogger("ltmap.dossier")

router = APIRouter(prefix="/api", tags=["dossier"])

ATTRIBUTION = "© OpenStreetMap contributors"
WD_REST = WIKIDATA_ITEMS
WD_TIMEOUT = 8.0

# ── Simple TTL cache ────────────────────────────────────────────────────────────
_cache: dict[str, tuple[float, object]] = {}
PLACE_TTL = 86400   # 24 hours
WD_TTL = 86400      # 24 hours
MAX_CACHE = 500


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _cache_set(key: str, value: object, ttl: float):
    if len(_cache) >= MAX_CACHE:
        # Evict oldest
        oldest = next(iter(_cache))
        _cache.pop(oldest, None)
    _cache[key] = (time.time() + ttl, value)


# ── Photon Reverse Geocoding ────────────────────────────────────────────────────

async def _place_at(lat: float, lng: float) -> dict | None:
    """Reverse geocode a coordinate via Photon (komoot's OSM geocoder)."""
    key = f"place:{lat:.3f},{lng:.3f}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=WD_TIMEOUT) as client:
                resp = await client.get(
                    f"{PHOTON_REVERSE}?lat={lat:.3f}&lon={lng:.3f}&lang=en&radius=50",
                    headers={"User-Agent": "LTMAP-OSINT/1.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    features = data.get("features", [])
                    if features:
                        props = features[0].get("properties", {})
                        result = {
                            "city": props.get("city", ""),
                            "state": props.get("state", ""),
                            "country": props.get("country", ""),
                            "countrycode": props.get("countrycode", ""),
                        }
                        _cache_set(key, result, PLACE_TTL)
                        return result
                    _cache_set(key, None, PLACE_TTL)
                    return None
        except Exception as exc:
            logger.warning("Photon reverse failed (attempt %d): %s", attempt, exc)
            if attempt == 1:
                await asyncio.sleep(0.8)

    _cache_set(key, None, 600)  # shorter TTL for failures
    return None


# ── Wikidata ────────────────────────────────────────────────────────────────────

def _still_holding(statements: list[dict]) -> list[dict]:
    """Filter statements that have not ended (no P582 qualifier)."""
    return [
        s for s in statements
        if not any(
            q.get("property", {}).get("id") == "P582"
            for q in (s.get("qualifiers") or [])
        )
    ]


def _current(statements: list[dict] | None) -> dict | None:
    """The currently-holding statement: preferred rank first, then non-ended."""
    if not statements:
        return None
    preferred = next((s for s in statements if s.get("rank") == "preferred"), None)
    if preferred:
        return preferred
    holding = _still_holding(statements)
    return holding[0] if holding else (statements[0] if statements else None)


def _item_id(s: dict | None) -> str:
    if not s:
        return ""
    content = (s.get("value") or {}).get("content", "")
    return content if isinstance(content, str) else ""


def _amount_of(s: dict | None) -> float | None:
    if not s:
        return None
    content = (s.get("value") or {}).get("content")
    if isinstance(content, dict):
        raw = content.get("amount")
        if raw is not None:
            try:
                n = float(raw)
                if not (n != n):  # isnan check
                    return n
            except (ValueError, TypeError):
                pass
    return None


async def _get_statements(qid: str) -> dict[str, list[dict]]:
    """Fetch all statements for a Wikidata item."""
    try:
        async with httpx.AsyncClient(timeout=WD_TIMEOUT) as client:
            resp = await client.get(
                f"{WD_REST}/{qid}/statements",
                headers={"User-Agent": "LTMAP-OSINT/1.0"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        logger.warning("Wikidata statements fetch failed: %s", exc)
    return {}


async def _labels_for(ids: list[str]) -> dict[str, str]:
    """Fetch English labels for a batch of Wikidata item IDs."""
    wanted = list(set(i for i in ids if i))
    if not wanted:
        return {}
    try:
        async with httpx.AsyncClient(timeout=WD_TIMEOUT) as client:
            resp = await client.get(
                f"{WIKIDATA_API}?action=wbgetentities&props=labels&languages=en&format=json&ids={'|'.join(wanted)}",
                headers={"User-Agent": "LTMAP-OSINT/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                out = {}
                for eid, entity in (data.get("entities") or {}).items():
                    label = (entity.get("labels") or {}).get("en", {}).get("value")
                    if label:
                        out[eid] = label
                return out
    except Exception as exc:
        logger.warning("Wikidata labels fetch failed: %s", exc)
    return {}


async def _read_country(country_name: str) -> dict | None:
    """Build country facts from Wikidata."""
    cache_key = f"wd:{country_name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Step 1: Get QID from Wikipedia
        async with httpx.AsyncClient(timeout=WD_TIMEOUT) as client:
            resp = await client.get(
                f"{WIKIPEDIA_API}?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&format=json&titles={quote(country_name)}",
                headers={"User-Agent": "LTMAP-OSINT/1.0"},
            )
            if resp.status_code != 200:
                return None
            pages = resp.json().get("query", {}).get("pages", {})
            qid = None
            for page in pages.values():
                qid = (page.get("pageprops") or {}).get("wikibase_item")
                break
            if not qid:
                return None

        # Step 2: Get statements
        s = await _get_statements(qid)
        capital = _item_id(_current(s.get("P36")))
        continent = _item_id(_current(s.get("P30")))
        languages = [_item_id(x) for x in _still_holding(s.get("P37", []))][:4]
        languages = [l for l in languages if l]
        currencies = [_item_id(x) for x in _still_holding(s.get("P38", []))][:3]
        currencies = [c for c in currencies if c]
        leader = _item_id(_current(s.get("P35")))
        position = _item_id(_current(s.get("P1906")))
        flag_file = _item_id(_current(s.get("P41")))

        # Step 3: Resolve labels
        ids_to_label = [capital, continent, leader, position] + languages + currencies
        names = await _labels_for(ids_to_label)

        result = {
            "country": {
                "name": country_name,
                "official_name": country_name,
                "capital": names.get(capital),
                "population": _amount_of(_current(s.get("P1082"))),
                "area": _amount_of(_current(s.get("P2046"))),
                "region": names.get(continent),
                "subregion": names.get(continent),
                "languages": [names[l] for l in languages if l in names],
                "currencies": [names[c] for c in currencies if c in names],
                "flag_url": f"{WIKIMEDIA_FILE}/{quote(flag_file)}" if flag_file else None,
                "timezones": [],
            },
            "head_of_state": (
                {"name": names[leader], "position": names.get(position) or "Head of State"}
                if leader and leader in names else None
            ),
        }
        _cache_set(cache_key, result, WD_TTL)
        return result

    except Exception as exc:
        logger.error("Read country error: %s", exc)
        return None


@router.get("/region-dossier")
async def get_region_dossier(
    lat: float = Query(0.0, description="Latitude"),
    lng: float = Query(0.0, description="Longitude"),
):
    """Provide country intelligence for a map coordinate."""
    try:
        # Step 1: Reverse geocode
        place = await _place_at(lat, lng)

        country_name = ""
        country_code = ""
        location_info: dict = {}

        if place:
            country_name = place.get("country", "")
            country_code = (place.get("countrycode") or "").upper()
            parts = [place.get("city", ""), place.get("state", ""), country_name]
            location_info = {
                "city": place.get("city", ""),
                "state": place.get("state", ""),
                "country": country_name,
                "country_code": country_code,
                "display_name": ", ".join(dict.fromkeys(p for p in parts if p)),
            }

        # Steps 2-3: Wikipedia + Wikidata in parallel
        wiki_query = location_info.get("city") or country_name

        async def fetch_wiki():
            if not wiki_query:
                return None
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        f"{WIKIPEDIA_SUMMARY}/{quote(wiki_query)}",
                        headers={"User-Agent": "LTMAP-OSINT/1.0"},
                    )
                    if resp.status_code == 200:
                        wiki = resp.json()
                        return {
                            "title": wiki.get("title"),
                            "extract": (wiki.get("extract") or "")[:500],
                            "thumbnail": (wiki.get("thumbnail") or {}).get("source"),
                        }
            except Exception as exc:
                logger.warning("Wikipedia fetch error: %s", exc)
            return None

        async def fetch_country():
            if not country_name:
                return None
            return await _read_country(country_name)

        wiki_result, wd_result = await asyncio.gather(
            fetch_wiki(), fetch_country(), return_exceptions=True
        )

        wiki_summary = wiki_result if not isinstance(wiki_result, Exception) else None
        facts = wd_result if not isinstance(wd_result, Exception) else None

        country_data = facts.get("country") if facts else None
        head_of_state = facts.get("head_of_state") if facts else None

        cache_ctl = "public, s-maxage=3600, stale-while-revalidate=7200" if place else "no-store"

        return JSONResponse(
            content={
                "coordinates": {"lat": lat, "lng": lng},
                "location": location_info,
                "country": country_data,
                "head_of_state": head_of_state,
                "wikipedia": wiki_summary,
                "attribution": ATTRIBUTION,
                "timestamp": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            },
            headers={"Cache-Control": cache_ctl},
        )

    except Exception as exc:
        logger.error("Region dossier error: %s", exc)
        return JSONResponse({"error": "Failed to fetch region data"}, status_code=500)
