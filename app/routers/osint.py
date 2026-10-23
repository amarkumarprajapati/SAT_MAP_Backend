"""FastAPI facade for the NestJS OSINT ingestion service."""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..config import settings

logger = logging.getLogger("ltmap.osint")
router = APIRouter(prefix="/api/osint", tags=["osint"])
_client: httpx.AsyncClient | None = None


async def open_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.OSINT_SERVICE_URL,
        timeout=httpx.Timeout(35.0, connect=5.0),
        follow_redirects=True,
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _forward(path: str, *, params=None):
    if _client is None:
        raise HTTPException(status_code=503, detail="OSINT service client unavailable")
    try:
        response = await _client.get(path, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code if exc.response.status_code < 500 else 502,
            detail="OSINT service request failed",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("OSINT service unavailable: %s", exc)
        raise HTTPException(status_code=502, detail="OSINT service unavailable") from exc


@router.get("/news")
async def get_osint_news(
    q: str | None = Query(None, max_length=200),
    limit: int = Query(10, ge=1, le=50),
):
    return await _forward("/osint/news", params={"q": q, "limit": limit})


@router.get("/news/stream")
async def stream_osint_news(request: Request):
    if _client is None:
        raise HTTPException(status_code=503, detail="OSINT service client unavailable")
    try:
        upstream_request = _client.build_request(
            "GET",
            "/osint/news/stream",
            headers={"Accept": "text/event-stream"},
            timeout=None,
        )
        upstream = await _client.send(upstream_request, stream=True)
        upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="OSINT news stream failed") from exc
    except httpx.HTTPError as exc:
        logger.warning("OSINT news stream unavailable: %s", exc)
        raise HTTPException(status_code=502, detail="OSINT news stream unavailable") from exc

    async def forward_events():
        try:
            async for chunk in upstream.aiter_raw():
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        forward_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/news/full")
async def get_osint_article(
    id: str | None = Query(None, max_length=500),
    url: str | None = Query(None, max_length=2048),
):
    if not id and not url:
        raise HTTPException(status_code=400, detail="Provide an article id or URL")
    return await _forward("/osint/news/full", params={"id": id, "url": url})


@router.get("/status")
async def osint_status():
    return await _forward("/osint/status")
