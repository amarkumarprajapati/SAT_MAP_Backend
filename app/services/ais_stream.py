"""
LTmap — AIS WebSocket Stream Manager

Maintains a persistent WebSocket connection to aisstream.io for live ship
tracking data. Ported from the frontend's maritime/route.ts WebSocket logic.

Ship records are kept in an in-memory dict, pruned on every snapshot build.
The connection auto-reconnects on drop.
"""

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from ..config import settings

logger = logging.getLogger("ltmap.ais")

# ── Ship Storage ────────────────────────────────────────────────────────────────

_ships: dict[int, dict[str, Any]] = {}
_ship_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_lock = asyncio.Lock()
_ws_task: asyncio.Task | None = None
_running = False
MAX_SHIPS = 20_000


def _get_ship_type(type_code: int) -> str:
    """Map AIS type code to LTmap ship category."""
    if not type_code:
        return "cargo"
    if 80 <= type_code <= 89:
        return "tanker"
    if 70 <= type_code <= 79:
        return "cargo"
    if type_code == 35:
        return "military"
    return "cargo"


async def _handle_message(raw: str) -> None:
    """Process one AIS message from the WebSocket stream."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return

    # AISStream sends subscription/authentication errors as JSON messages
    # without MetaData.MMSI. Log them instead of silently discarding them as
    # malformed vessel updates, which made a dead feed look like an empty sea.
    if parsed.get("error"):
        logger.error("AIS provider rejected the stream: %s", parsed.get("error"))
        return

    mmsi = parsed.get("MetaData", {}).get("MMSI")
    if not mmsi:
        return

    async with _lock:
        existing = _ships.get(mmsi, {"id": mmsi, "mmsi": mmsi, "timestamp": time.time()})

        meta = parsed.get("MetaData", {})
        if meta.get("ShipName"):
            existing["name"] = meta["ShipName"].strip()

        msg_type = parsed.get("MessageType", "")

        if msg_type == "PositionReport":
            report = parsed.get("Message", {}).get("PositionReport", {})
            if report:
                lat = report.get("Latitude")
                lng = report.get("Longitude")
                if lat is not None and lng is not None:
                    if existing.get("firstSeenLat") is None or existing.get("firstSeenLng") is None:
                        existing["firstSeenLat"] = lat
                        existing["firstSeenLng"] = lng
                        existing["firstSeenAt"] = time.time()
                    existing["lat"] = lat
                    existing["lng"] = lng
                existing["speed"] = report.get("Sog")
                true_heading = report.get("TrueHeading")
                course = report.get("Cog")
                existing["heading"] = true_heading if true_heading is not None and 0 <= true_heading < 360 else course
                existing["timestamp"] = time.time()

        elif msg_type == "ShipStaticData":
            static = parsed.get("Message", {}).get("ShipStaticData", {})
            if static:
                if static.get("Name"):
                    existing["name"] = static["Name"].strip()
                if static.get("Destination"):
                    existing["destination"] = static["Destination"].strip()
                existing["type"] = _get_ship_type(static.get("Type", 0))

        # Only store if we have a position
        if existing.get("lat") is not None and existing.get("lng") is not None:
            _ships[mmsi] = existing
            update = existing.copy()
            for queue in tuple(_ship_subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(update)
                except asyncio.QueueFull:
                    pass

        # Evict oldest if too many
        if len(_ships) > MAX_SHIPS:
            oldest = next(iter(_ships))
            _ships.pop(oldest, None)


async def ingest_external_ship(mmsi: int, fields: dict[str, Any]) -> None:
    """Merge a position or metadata update from a secondary AIS provider."""
    async with _lock:
        existing = _ships.get(mmsi, {"id": mmsi, "mmsi": mmsi, "timestamp": time.time()})
        existing.update({key: value for key, value in fields.items() if value is not None})
        if existing.get("lat") is not None and existing.get("lng") is not None:
            existing["timestamp"] = time.time()
            _ships[mmsi] = existing
            update = existing.copy()
            for queue in tuple(_ship_subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(update)
                except asyncio.QueueFull:
                    pass


async def _connect_loop() -> None:
    """Persistent WebSocket connection with auto-reconnect."""
    global _running

    api_key = settings.AIS_API_KEY
    if not api_key:
        logger.warning("AIS_API_KEY not set — maritime ship stream disabled")
        return

    _running = True
    while _running:
        try:
            logger.info("Connecting to aisstream.io...")
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream",
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                # Subscribe with bounding boxes and filters
                sub = {
                    "APIKey": api_key,
                    "BoundingBoxes": [
                        [[34.8, 139.5], [35.7, 140.2]],
                        [[25.0, 54.0], [27.5, 57.5]],
                        [[27.0, 32.0], [32.0, 33.5]],
                        [[12.0, 42.5], [14.0, 44.0]],
                        [[8.0, -80.5], [10.0, -79.0]],
                        [[1.0, 103.0], [3.0, 104.5]],
                        [[22.0, 118.0], [26.0, 121.0]],
                        [[50.0, 0.0], [53.0, 5.0]],
                        [[33.0, -119.0], [34.5, -117.0]],
                    ],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }
                await ws.send(json.dumps(sub))
                logger.info("AIS stream connected and subscribed")

                async for message in ws:
                    if not _running:
                        break
                    await _handle_message(str(message))

        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(
                "AIS WebSocket closed (code=%s, reason=%s), reconnecting in 5s...",
                exc.code,
                exc.reason or "no reason supplied",
            )
        except Exception as exc:
            logger.warning("AIS WebSocket error: %s, reconnecting in 5s...", exc)

        if _running:
            await asyncio.sleep(5)


# ── Public API ──────────────────────────────────────────────────────────────────

async def start() -> None:
    """Start the AIS stream background task (called on app startup)."""
    global _ws_task
    if _ws_task is None or _ws_task.done():
        _ws_task = asyncio.create_task(_connect_loop())
        logger.info("AIS stream background task started")


async def stop() -> None:
    """Gracefully stop the AIS stream (called on app shutdown)."""
    global _running, _ws_task
    _running = False
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
    _ws_task = None
    logger.info("AIS stream stopped")


async def get_ships() -> list[dict[str, Any]]:
    """Return a snapshot of all tracked ships, pruning stale entries."""
    now = time.time()
    stale_cutoff = now - 10 * 60  # 10 minutes

    async with _lock:
        # Prune stale
        stale_keys = [k for k, v in _ships.items() if v.get("timestamp", 0) < stale_cutoff]
        for k in stale_keys:
            del _ships[k]
        return list(_ships.values())


def subscribe_ships() -> asyncio.Queue[dict[str, Any]]:
    """Subscribe to incremental AIS position updates."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
    _ship_subscribers.add(queue)
    return queue


def unsubscribe_ships(queue: asyncio.Queue[dict[str, Any]]) -> None:
    _ship_subscribers.discard(queue)


def ship_count() -> int:
    """Number of currently tracked ships (lock-free, for stats)."""
    return len(_ships)
