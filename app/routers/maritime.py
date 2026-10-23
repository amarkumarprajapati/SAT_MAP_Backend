"""
LTmap — Maritime Router

Serves live AIS ship positions (from the ais_stream service), global major
ports with congestion analysis, and strategic chokepoints with live traffic
counts. Ported from maritime/route.ts.
"""

import asyncio
import math
import time
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..services import ais_stream

logger = logging.getLogger("ltmap.maritime")

router = APIRouter(prefix="/api/maritime", tags=["maritime"])


@router.websocket("/stream")
async def maritime_stream(websocket: WebSocket, mmsi: str | None = None):
    """
    Focused AIS stream.
    Supports on-demand vessel tracking so client is not overloaded with 20,000 ships.
    Clients can send {"action": "track", "mmsi": ...} or {"action": "untrack"}.
    """
    await websocket.accept()
    queue = ais_stream.subscribe_ships()
    tracked_mmsi: str | None = str(mmsi) if mmsi else None

    # If MMSI was requested on connect, send its current snapshot immediately
    if tracked_mmsi:
        current = await ais_stream.get_ship(tracked_mmsi)
        if current:
            await websocket.send_json({"type": "ship_update", "ship": current})

    async def incoming_listener():
        nonlocal tracked_mmsi
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    action = msg.get("action")
                    if action == "track":
                        target = msg.get("mmsi")
                        if target:
                            tracked_mmsi = str(target)
                            current = await ais_stream.get_ship(tracked_mmsi)
                            if current:
                                await websocket.send_json({"type": "ship_update", "ship": current})
                    elif action == "untrack":
                        tracked_mmsi = None
                except Exception:
                    pass
        except Exception:
            pass

    listener_task = asyncio.create_task(incoming_listener())

    try:
        while True:
            ship = await queue.get()
            # Only broadcast if this ship matches the tracked MMSI
            if tracked_mmsi:
                ship_mmsi = str(ship.get("mmsi") or ship.get("id") or "")
                if ship_mmsi == tracked_mmsi:
                    await websocket.send_json({"type": "ship_update", "ship": ship})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        listener_task.cancel()
        ais_stream.unsubscribe_ships(queue)

# ── Static Data ─────────────────────────────────────────────────────────────────
PORTS = [
    {"name": "Shanghai", "country": "CN", "lat": 31.23, "lng": 121.47, "type": "container", "volume": "47.3M TEU", "rank": 1},
    {"name": "Singapore", "country": "SG", "lat": 1.26, "lng": 103.84, "type": "container", "volume": "37.2M TEU", "rank": 2},
    {"name": "Ningbo-Zhoushan", "country": "CN", "lat": 29.87, "lng": 121.55, "type": "container", "volume": "33.3M TEU", "rank": 3},
    {"name": "Shenzhen", "country": "CN", "lat": 22.54, "lng": 114.05, "type": "container", "volume": "30.0M TEU", "rank": 4},
    {"name": "Guangzhou", "country": "CN", "lat": 23.08, "lng": 113.32, "type": "container", "volume": "24.2M TEU", "rank": 5},
    {"name": "Busan", "country": "KR", "lat": 35.10, "lng": 129.04, "type": "container", "volume": "22.7M TEU", "rank": 6},
    {"name": "Qingdao", "country": "CN", "lat": 36.07, "lng": 120.38, "type": "container", "volume": "22.0M TEU", "rank": 7},
    {"name": "Rotterdam", "country": "NL", "lat": 51.90, "lng": 4.50, "type": "container", "volume": "14.5M TEU", "rank": 8},
    {"name": "Tokyo", "country": "JP", "lat": 35.61, "lng": 139.79, "type": "container", "volume": "4.5M TEU"},
    {"name": "Yokohama", "country": "JP", "lat": 35.45, "lng": 139.66, "type": "container", "volume": "2.9M TEU"},
    {"name": "Kobe", "country": "JP", "lat": 34.67, "lng": 135.21, "type": "container", "volume": "2.8M TEU"},
    {"name": "Nagoya", "country": "JP", "lat": 35.08, "lng": 136.87, "type": "container", "volume": "2.6M TEU"},
    {"name": "Osaka", "country": "JP", "lat": 34.63, "lng": 135.41, "type": "container", "volume": "2.1M TEU"},
    {"name": "Hakata (Fukuoka)", "country": "JP", "lat": 33.60, "lng": 130.40, "type": "container", "volume": "0.9M TEU"},
    {"name": "Kitakyushu", "country": "JP", "lat": 33.91, "lng": 130.93, "type": "container", "volume": "0.5M TEU"},
    {"name": "Shimizu", "country": "JP", "lat": 35.00, "lng": 138.50, "type": "container", "volume": "0.5M TEU"},
    {"name": "Tomakomai", "country": "JP", "lat": 42.63, "lng": 141.63, "type": "container", "volume": "0.4M TEU"},
    {"name": "Niigata", "country": "JP", "lat": 37.95, "lng": 139.06, "type": "container", "volume": "0.2M TEU"},
    {"name": "Sendai", "country": "JP", "lat": 38.27, "lng": 141.02, "type": "container", "volume": "0.2M TEU"},
    {"name": "Mizushima", "country": "JP", "lat": 34.50, "lng": 133.72, "type": "energy", "volume": "Industrial"},
    {"name": "Yokkaichi", "country": "JP", "lat": 34.95, "lng": 136.65, "type": "energy", "volume": "Industrial"},
    {"name": "Dubai (Jebel Ali)", "country": "AE", "lat": 25.01, "lng": 55.06, "type": "container", "volume": "14.0M TEU", "rank": 9},
    {"name": "Port Klang", "country": "MY", "lat": 2.99, "lng": 101.39, "type": "container", "volume": "13.2M TEU", "rank": 10},
    {"name": "Antwerp", "country": "BE", "lat": 51.30, "lng": 4.40, "type": "container", "volume": "12.0M TEU", "rank": 11},
    {"name": "Xiamen", "country": "CN", "lat": 24.48, "lng": 118.09, "type": "container", "volume": "11.4M TEU", "rank": 12},
    {"name": "Hamburg", "country": "DE", "lat": 53.55, "lng": 9.97, "type": "container", "volume": "8.7M TEU", "rank": 14},
    {"name": "Los Angeles", "country": "US", "lat": 33.74, "lng": -118.27, "type": "container", "volume": "9.9M TEU", "rank": 13},
    {"name": "Long Beach", "country": "US", "lat": 33.75, "lng": -118.19, "type": "container", "volume": "8.0M TEU", "rank": 15},
    {"name": "Tanjung Pelepas", "country": "MY", "lat": 1.36, "lng": 103.55, "type": "container", "volume": "9.8M TEU", "rank": 16},
    {"name": "Savannah", "country": "US", "lat": 32.08, "lng": -81.09, "type": "container", "volume": "5.6M TEU", "rank": 20},
    {"name": "Felixstowe", "country": "GB", "lat": 51.96, "lng": 1.35, "type": "container", "volume": "3.8M TEU", "rank": 25},
    {"name": "Santos", "country": "BR", "lat": -23.95, "lng": -46.31, "type": "container", "volume": "4.2M TEU", "rank": 22},
    {"name": "Colombo", "country": "LK", "lat": 6.94, "lng": 79.84, "type": "container", "volume": "7.2M TEU", "rank": 17},
    {"name": "Ras Tanura", "country": "SA", "lat": 26.64, "lng": 50.16, "type": "energy", "volume": "6.5M bpd"},
    {"name": "Fujairah", "country": "AE", "lat": 25.14, "lng": 56.35, "type": "energy", "volume": "3.5M bpd"},
    {"name": "Novorossiysk", "country": "RU", "lat": 44.72, "lng": 37.77, "type": "energy", "volume": "2.8M bpd"},
    {"name": "Houston Ship Channel", "country": "US", "lat": 29.73, "lng": -95.27, "type": "energy", "volume": "2.5M bpd"},
    {"name": "Kharg Island", "country": "IR", "lat": 29.24, "lng": 50.33, "type": "energy", "volume": "2.0M bpd"},
    {"name": "Primorsk", "country": "RU", "lat": 60.35, "lng": 28.70, "type": "energy", "volume": "1.6M bpd"},
    {"name": "Norfolk Naval Station", "country": "US", "lat": 36.95, "lng": -76.33, "type": "naval", "fleet": "US Atlantic Fleet"},
    {"name": "San Diego Naval Base", "country": "US", "lat": 32.69, "lng": -117.15, "type": "naval", "fleet": "US Pacific Fleet"},
    {"name": "Pearl Harbor", "country": "US", "lat": 21.35, "lng": -157.97, "type": "naval", "fleet": "US Pacific Fleet"},
    {"name": "Yokosuka", "country": "JP", "lat": 35.28, "lng": 139.67, "type": "naval", "fleet": "US 7th Fleet"},
    {"name": "Severomorsk", "country": "RU", "lat": 69.07, "lng": 33.42, "type": "naval", "fleet": "Russian Northern Fleet"},
    {"name": "Tartus", "country": "SY", "lat": 34.89, "lng": 35.89, "type": "naval", "fleet": "Russian Mediterranean"},
    {"name": "Zhanjiang", "country": "CN", "lat": 21.20, "lng": 110.39, "type": "naval", "fleet": "PLA Navy South Sea Fleet"},
    {"name": "Qingdao Naval", "country": "CN", "lat": 36.09, "lng": 120.43, "type": "naval", "fleet": "PLA Navy North Sea Fleet"},
    {"name": "Portsmouth", "country": "GB", "lat": 50.80, "lng": -1.11, "type": "naval", "fleet": "Royal Navy"},
    {"name": "Toulon", "country": "FR", "lat": 43.12, "lng": 5.93, "type": "naval", "fleet": "French Navy Mediterranean"},
    {"name": "Changi Naval Base", "country": "SG", "lat": 1.33, "lng": 104.01, "type": "naval", "fleet": "Republic of Singapore Navy"},
    {"name": "Visakhapatnam", "country": "IN", "lat": 17.69, "lng": 83.30, "type": "naval", "fleet": "Indian Navy Eastern Command"},
    {"name": "Mumbai Naval", "country": "IN", "lat": 18.93, "lng": 72.84, "type": "naval", "fleet": "Indian Navy Western Command"},
]

CHOKEPOINTS = [
    {"name": "Strait of Hormuz", "lat": 26.57, "lng": 56.25, "traffic": "21M bpd oil", "risk": "HIGH"},
    {"name": "Strait of Malacca", "lat": 2.50, "lng": 101.50, "traffic": "16M bpd oil", "risk": "MODERATE"},
    {"name": "Suez Canal", "lat": 30.43, "lng": 32.34, "traffic": "12% world trade", "risk": "ELEVATED"},
    {"name": "Bab el-Mandeb", "lat": 12.58, "lng": 43.33, "traffic": "6.2M bpd oil", "risk": "CRITICAL"},
    {"name": "Panama Canal", "lat": 9.08, "lng": -79.68, "traffic": "5% world trade", "risk": "LOW"},
    {"name": "Turkish Straits", "lat": 41.12, "lng": 29.07, "traffic": "3M bpd oil", "risk": "MODERATE"},
    {"name": "Danish Straits", "lat": 55.70, "lng": 12.60, "traffic": "3.2M bpd oil", "risk": "LOW"},
    {"name": "Cape of Good Hope", "lat": -34.36, "lng": 18.47, "traffic": "Alt route Suez", "risk": "LOW"},
    {"name": "Taiwan Strait", "lat": 24.00, "lng": 119.00, "traffic": "88% large ships", "risk": "ELEVATED"},
    {"name": "Lombok Strait", "lat": -8.47, "lng": 115.72, "traffic": "Alt Malacca", "risk": "LOW"},
]


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Fast approximate distance in km (equirectangular)."""
    dx = (lng1 - lng2) * math.cos(math.radians((lat1 + lat2) / 2))
    dy = lat1 - lat2
    return math.sqrt(dx * dx + dy * dy) * 111.32


# ── Snapshot Cache ──────────────────────────────────────────────────────────────
_snapshot_cache: dict[bool, dict] = {}
_snapshot_time: dict[bool, float] = {}
SNAPSHOT_TTL = 5.0  # seconds


async def _build_snapshot(include_ports: bool = True) -> dict:
    """Build a complete maritime snapshot with dynamic port/chokepoint analysis."""
    ships = await ais_stream.get_ships()
    now = time.time()

    # Port and chokepoint analysis is expensive for large AIS feeds. Skip it
    # for ship-only requests; those requests do not use either overlay.
    dynamic_ports = []
    dynamic_chokepoints = []
    if include_ports:
        for port in PORTS:
            nearby_count = 0
            waiting_count = 0
            for s in ships:
                s_lat = s.get("lat")
                s_lng = s.get("lng")
                if s_lat is None or s_lng is None:
                    continue
                if _distance_km(port["lat"], port["lng"], s_lat, s_lng) < 50:
                    nearby_count += 1
                    if (s.get("speed") or 0) < 0.5 and s.get("type") != "military":
                        waiting_count += 1

            congestion_ratio = waiting_count / nearby_count if nearby_count > 0 else 0
            if congestion_ratio > 0.6 or waiting_count > 30:
                congestion_status = "SEVERE"
                est_dwell = "7+ Days"
            elif congestion_ratio > 0.4 or waiting_count > 15:
                congestion_status = "CONGESTED"
                est_dwell = "3-5 Days"
            else:
                congestion_status = "NORMAL"
                est_dwell = "1-2 Days"

            volume_text = port.get("volume", "")
            if volume_text:
                volume_text = f"{volume_text} | LIVE: {nearby_count} (WAITING: {waiting_count})"
            else:
                volume_text = f"LIVE: {nearby_count} (WAITING: {waiting_count})"

            dynamic_ports.append({
                **port,
                "volume": volume_text,
                "congestion": congestion_status,
                "dwell_time": est_dwell,
            })

        for choke in CHOKEPOINTS:
            nearby_count = 0
            for s in ships:
                s_lat = s.get("lat")
                s_lng = s.get("lng")
                if s_lat is not None and s_lng is not None:
                    if _distance_km(choke["lat"], choke["lng"], s_lat, s_lng) < 100:
                        nearby_count += 1

            risk = choke["risk"]
            if nearby_count > 50:
                risk = "CRITICAL"
            elif nearby_count > 20 and risk != "CRITICAL":
                risk = "HIGH"
            elif nearby_count > 5 and risk == "LOW":
                risk = "ELEVATED"

            dynamic_chokepoints.append({
                **choke,
                "traffic": f"{choke['traffic']} | LIVE SHIPS: {nearby_count}",
                "risk": risk,
            })

    cargo_count = sum(1 for s in ships if s.get("type") in ("cargo", "container") or not s.get("type"))
    tanker_count = sum(1 for s in ships if s.get("type") == "tanker")
    military_count = sum(1 for s in ships if s.get("type") == "military")

    return {
        "ports": dynamic_ports,
        "chokepoints": dynamic_chokepoints,
        "ships": ships,
        "total_ports": len(dynamic_ports),
        "total_chokepoints": len(dynamic_chokepoints),
        "total_ships": len(ships),
        "category_counts": {
            "cargo": cargo_count,
            "tanker": tanker_count,
            "military": military_count,
        },
        "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
    }


@router.get("")
async def get_maritime_data(
    type: str | None = None,
    types: str | None = None,
    include_ships: bool = True,
    include_ports: bool = True,
    west: float | None = Query(None, ge=-540, le=540),
    south: float | None = Query(None, ge=-90, le=90),
    east: float | None = Query(None, ge=-540, le=540),
    north: float | None = Query(None, ge=-90, le=90),
):
    """Fetch live AIS maritime data: vessels, ports, and chokepoints.
    Optionally filter ships by category: 'container' / 'cargo', 'tanker', 'military', 'all'.
    """
    global _snapshot_cache, _snapshot_time

    # Return immediately when every maritime layer is off, without polling AIS.
    if not include_ships and not include_ports:
        return Response(
            content=json.dumps({
                "ports": [], "chokepoints": [], "ships": [],
                "total_ports": 0, "total_chokepoints": 0, "total_ships": 0,
                "category_counts": {"cargo": 0, "tanker": 0, "military": 0},
                "timestamp": datetime.now(timezone.utc).isoformat(), "filter": "all",
            }),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    now = time.time()
    cache_key = bool(include_ports)
    if cache_key in _snapshot_cache and now - _snapshot_time.get(cache_key, 0) < SNAPSHOT_TTL:
        snapshot = _snapshot_cache[cache_key]
    else:
        snapshot = await _build_snapshot(include_ports=include_ports)
        _snapshot_cache[cache_key] = snapshot
        _snapshot_time[cache_key] = now

    requested_types = {
        value.strip().lower() for value in (types or "").split(",") if value.strip()
    }
    if requested_types:
        filtered_ships = [
            ship for ship in snapshot["ships"]
            if ("cargo" in requested_types and ship.get("type") in ("cargo", "container"))
            or ("tanker" in requested_types and ship.get("type") == "tanker")
            or ("military" in requested_types and ship.get("type") == "military")
        ]
    elif type and type.lower() != "all":
        req_type = type.lower()
        if req_type in ("container", "cargo"):
            filtered_ships = [s for s in snapshot["ships"] if s.get("type") in ("cargo", "container")]
        else:
            filtered_ships = [s for s in snapshot["ships"] if s.get("type") == req_type]
    else:
        filtered_ships = snapshot["ships"] if include_ships else []

    response_data = {
        **snapshot,
        "ships": filtered_ships,
        "ports": snapshot["ports"] if include_ports else [],
        "chokepoints": snapshot["chokepoints"] if include_ports else [],
        "total_ships": len(filtered_ships),
        "total_ports": len(snapshot["ports"]) if include_ports else 0,
        "total_chokepoints": len(snapshot["chokepoints"]) if include_ports else 0,
        "filter": sorted(requested_types) if requested_types else (type or "all"),
    }

    if None not in (west, south, east, north):
        def in_view(item: dict) -> bool:
            try:
                lat = float(item.get("lat"))
                lng = float(item.get("lng"))
            except (TypeError, ValueError):
                return False
            if abs(east - west) >= 360:
                inside_lon = True
            else:
                west_norm = ((west + 180) % 360) - 180
                east_norm = ((east + 180) % 360) - 180
                inside_lon = west_norm <= lng <= east_norm if west_norm <= east_norm else lng >= west_norm or lng <= east_norm
            return south <= lat <= north and inside_lon

        response_data["ships"] = [s for s in response_data["ships"] if in_view(s)]
        response_data["ports"] = [p for p in response_data["ports"] if in_view(p)]
        response_data["chokepoints"] = [p for p in response_data["chokepoints"] if in_view(p)]
        response_data["total_ships"] = len(response_data["ships"])
        response_data["total_ports"] = len(response_data["ports"])
        response_data["total_chokepoints"] = len(response_data["chokepoints"])
    response_data["category_counts"] = {
        "cargo": sum(1 for ship in response_data["ships"] if ship.get("type") in ("cargo", "container")),
        "tanker": sum(1 for ship in response_data["ships"] if ship.get("type") == "tanker"),
        "military": sum(1 for ship in response_data["ships"] if ship.get("type") == "military"),
    }

    body = json.dumps(response_data)

    max_age = int(SNAPSHOT_TTL)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": f"public, max-age={max_age}, s-maxage={max_age}, stale-while-revalidate=15",
        },
    )
