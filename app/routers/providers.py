"""Small, allowlisted proxy endpoints for browser-required map and reference data."""

import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from ..urls import (
    ARCGIS_IMAGERY_TILE, BIGDATACLOUD_REVERSE, CARTO_DARK_STYLE,
    CARTO_TILE_HOST, TERRARIUM_TILE, USGS_EARTHQUAKES,
    WIKIPEDIA_API, WIKIPEDIA_SUMMARY,
)

logger = logging.getLogger("ltmap.providers")
router = APIRouter(prefix="/api", tags=["providers"])

_client: httpx.AsyncClient | None = None


async def open_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=4.0),
        headers={"User-Agent": "LTmap/1.0 (map data proxy)"},
        follow_redirects=True,
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _get(url: str, *, accept: str = "application/json") -> httpx.Response:
    if _client is None:
        raise HTTPException(status_code=503, detail="Provider client unavailable")
    try:
        response = await _client.get(url, headers={"Accept": accept})
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Upstream provider error") from exc
    except httpx.HTTPError as exc:
        logger.warning("Provider request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Upstream provider unavailable") from exc


def _wiki_payload(data: dict, fallback_title: str) -> dict | None:
    if data.get("type") == "disambiguation" or not data.get("extract"):
        return None
    return {
        "title": data.get("title", fallback_title),
        "description": data.get("description") or "Active Spacecraft Payload",
        "extract": data["extract"],
        "thumbnailUrl": (data.get("originalimage") or data.get("thumbnail") or {}).get("source"),
        "wikiUrl": (data.get("content_urls") or {}).get("desktop", {}).get("page")
        or f"https://en.wikipedia.org/wiki/{quote(fallback_title, safe='')}",
        "source": "Wikipedia",
    }


@router.get("/satellite-info")
async def satellite_info(name: str = Query(..., min_length=1, max_length=160)):
    clean = re.sub(r"\s+F\d+|\s+DEB.*|\s+R/B.*", "", name, flags=re.I).strip()
    query_name = clean
    if re.search(r"ISS|ZARYA", clean, re.I):
        query_name = "International Space Station"
    elif re.search(r"TIANGONG", clean, re.I):
        query_name = "Tiangong space station"
    elif re.search(r"STARLINK", clean, re.I):
        query_name = "Starlink"
    elif re.search(r"ONEWEB", clean, re.I):
        query_name = "OneWeb satellite constellation"

    for term in dict.fromkeys((query_name, clean + " satellite", clean)):
        if term == query_name:
            response = await _get(f"{WIKIPEDIA_SUMMARY}/{quote(term, safe='')}")
            payload = _wiki_payload(response.json(), term)
            if payload:
                return payload
        else:
            response = await _get(
                f"{WIKIPEDIA_API}?action=query&list=search&srsearch={quote(term)}&utf8=&format=json"
            )
            titles = response.json().get("query", {}).get("search", [])
            if titles:
                summary = await _get(f"{WIKIPEDIA_SUMMARY}/{quote(titles[0]['title'], safe='')}")
                payload = _wiki_payload(summary.json(), titles[0]["title"])
                if payload:
                    return payload
    return {
        "title": name,
        "description": "Active Orbital Intelligence Asset",
        "extract": f"{name} is an operational spacecraft currently tracked in Earth orbit.",
        "thumbnailUrl": None,
        "wikiUrl": f"https://en.wikipedia.org/wiki/Special:Search?search={quote(clean)}",
        "source": "Space Catalogue",
    }


@router.get("/geocode/reverse")
async def reverse_geocode(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    response = await _get(
        f"{BIGDATACLOUD_REVERSE}?latitude={latitude}&longitude={longitude}&localityLanguage=en"
    )
    return response.json()


@router.get("/earthquakes")
async def earthquakes():
    response = await _get(
        USGS_EARTHQUAKES
    )
    return Response(
        content=response.content,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=300"},
    )


@router.get("/map/style")
async def map_style():
    response = await _get(CARTO_DARK_STYLE)
    style = response.json()
    # MapLibre resolves relative style assets against the style URL. Rewrite each
    # Carto resource URL to the same-origin backend proxy.
    def rewrite(value):
        if isinstance(value, str) and CARTO_TILE_HOST in value:
            return "/api/map/carto?url=" + quote(value, safe="")
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value
    return rewrite(style)


@router.get("/map/carto")
async def map_carto_asset(url: str = Query(..., min_length=1, max_length=2048)):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (
        parsed.hostname == CARTO_TILE_HOST or (parsed.hostname or "").endswith(f".{CARTO_TILE_HOST}")
    ):
        raise HTTPException(status_code=403, detail="Unsupported map asset host")
    if _client is None:
        raise HTTPException(status_code=503, detail="Provider client unavailable")
    try:
        response = await _client.get(url, headers={"Accept": "*/*"})
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Map tile provider error") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Map tile provider unavailable") from exc
    content_type = response.headers.get("content-type", "application/octet-stream")
    return Response(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/map/imagery/{z}/{y}/{x}")
async def imagery_tile(z: int, y: int, x: int):
    if not 0 <= z <= 18 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise HTTPException(status_code=404, detail="Tile outside supported range")
    response = await _get(
        ARCGIS_IMAGERY_TILE.format(z=z, y=y, x=x),
        accept="image/*",
    )
    return Response(response.content, media_type=response.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/map/terrain/{z}/{x}/{y}.png")
async def terrain_tile(z: int, x: int, y: int):
    if not 0 <= z <= 10 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise HTTPException(status_code=404, detail="Tile outside supported range")
    response = await _get(
        TERRARIUM_TILE.format(z=z, x=x, y=y),
        accept="image/png",
    )
    return Response(response.content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
