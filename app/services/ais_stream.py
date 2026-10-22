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
import math
from typing import Any

import websockets

from ..config import settings
from ..urls import AISSTREAM_WEBSOCKET

logger = logging.getLogger("ltmap.ais")

# ── Ship Storage ────────────────────────────────────────────────────────────────

_ships: dict[int, dict[str, Any]] = {}
_ship_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_lock = asyncio.Lock()
_ws_task: asyncio.Task | None = None
_running = False
MAX_SHIPS = 20_000
MAX_TRACK_POINTS = 500


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two AIS positions."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def _record_position(existing: dict[str, Any], lat: float, lng: float, seen_at: float) -> None:
    """Append a valid, non-duplicate position and update observed journey stats."""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return
    track = existing.setdefault("track", [])
    if track and seen_at - float(track[-1].get("timestamp", 0)) < 1:
        track[-1] = {"lat": lat, "lng": lng, "timestamp": seen_at, "speed": existing.get("speed")}
        existing["lat"], existing["lng"] = lat, lng
        existing["lastSeenAt"] = seen_at
        return
    if track:
        previous = track[-1]
        segment = _distance_km(float(previous["lat"]), float(previous["lng"]), lat, lng)
        # Drop obvious bad jumps; they must not inflate reported vessel travel.
        elapsed_hours = max((seen_at - float(previous["timestamp"])) / 3600, 1 / 3600)
        if segment <= 1000 and segment / elapsed_hours <= 90:
            existing["trackDistanceKm"] = float(existing.get("trackDistanceKm", 0)) + segment
        else:
            segment = 0
    else:
        existing["trackStartLat"] = lat
        existing["trackStartLng"] = lng
        existing["trackStartAt"] = seen_at
        existing["trackDistanceKm"] = 0.0
    track.append({"lat": lat, "lng": lng, "timestamp": seen_at, "speed": existing.get("speed")})
    if len(track) > MAX_TRACK_POINTS:
        del track[:-MAX_TRACK_POINTS]
    existing["lat"], existing["lng"] = lat, lng
    existing["lastSeenAt"] = seen_at
    existing["trackPointCount"] = int(existing.get("trackPointCount", 0)) + 1


def _get_ship_type(type_code: int) -> str:
    """Map AIS type code to LTmap ship category."""
    if not type_code:
        return "cargo"
    if 80 <= type_code <= 89:
        return "tanker"
    if 70 <= type_code <= 79:
        return "cargo"
    if type_code in (35, 55, 50, 51):
        return "military"
    return "cargo"


async def _handle_message(raw: str | bytes) -> None:
    """Process one AIS message from the WebSocket stream."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        parsed = json.loads(raw)
    except Exception as e:
        logger.debug("Failed to decode AIS message: %s", e)
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
        existing = _ships.get(mmsi, {"id": mmsi, "mmsi": mmsi, "type": "cargo", "timestamp": time.time(), "track": [], "trackPointCount": 0, "trackDistanceKm": 0.0})

        meta = parsed.get("MetaData", {})
        if meta.get("ShipName"):
            existing["name"] = meta["ShipName"].strip()

        # MetaData frequently carries real-time latitude & longitude
        meta_lat = meta.get("latitude")
        meta_lng = meta.get("longitude")
        if meta_lat is not None and meta_lng is not None and (meta_lat != 0 or meta_lng != 0) and existing.get("lat") is None:
            if existing.get("firstSeenLat") is None or existing.get("firstSeenLng") is None:
                existing["firstSeenLat"] = meta_lat
                existing["firstSeenLng"] = meta_lng
                existing["firstSeenAt"] = time.time()
            _record_position(existing, float(meta_lat), float(meta_lng), time.time())

        msg_type = parsed.get("MessageType", "")

        if msg_type == "PositionReport":
            report = parsed.get("Message", {}).get("PositionReport", {})
            if report:
                lat = report.get("Latitude")
                lng = report.get("Longitude")
                if lat is not None and lng is not None and (lat != 0 or lng != 0):
                    if existing.get("firstSeenLat") is None or existing.get("firstSeenLng") is None:
                        existing["firstSeenLat"] = lat
                        existing["firstSeenLng"] = lng
                        existing["firstSeenAt"] = time.time()
                existing["speed"] = report.get("Sog")
                if lat is not None and lng is not None:
                    _record_position(existing, float(lat), float(lng), time.time())
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
        existing = _ships.get(mmsi, {"id": mmsi, "mmsi": mmsi, "timestamp": time.time(), "track": [], "trackPointCount": 0, "trackDistanceKm": 0.0})
        lat, lng = fields.get("lat"), fields.get("lng")
        existing.update({key: value for key, value in fields.items() if value is not None})
        if lat is not None and lng is not None:
            _record_position(existing, float(lat), float(lng), time.time())
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
                AISSTREAM_WEBSOCKET,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                # Global bounding box for worldwide maritime visibility
                sub = {
                    "APIKey": api_key,
                    "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }
                await ws.send(json.dumps(sub))
                logger.info("AIS stream connected and subscribed (global coverage)")

                async for message in ws:
                    if not _running:
                        break
                    await _handle_message(message)

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
    stale_cutoff = now - 60 * 60  # 60 minutes

    async with _lock:
        # Prune stale
        stale_keys = [k for k, v in _ships.items() if v.get("timestamp", 0) < stale_cutoff]
        for k in stale_keys:
            del _ships[k]
        return list(_ships.values())


async def get_ship(mmsi: int | str) -> dict[str, Any] | None:
    """Return a single ship record by MMSI."""
    try:
        int_mmsi = int(mmsi)
    except (ValueError, TypeError):
        int_mmsi = None

    async with _lock:
        if int_mmsi is not None and int_mmsi in _ships:
            return _ships[int_mmsi].copy()
        for k, v in _ships.items():
            if str(k) == str(mmsi) or str(v.get("id")) == str(mmsi) or str(v.get("mmsi")) == str(mmsi):
                return v.copy()
        return None


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
