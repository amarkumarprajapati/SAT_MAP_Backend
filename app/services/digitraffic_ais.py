"""Free AIS supplement from Fintraffic Digitraffic (Finnish waters)."""

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

import paho.mqtt.client as mqtt

from . import ais_stream
from ..urls import DIGITRAFFIC_MQTT_HOST, DIGITRAFFIC_MQTT_PATH, DIGITRAFFIC_MQTT_PORT

logger = logging.getLogger("ltmap.digitraffic")
_task: asyncio.Task | None = None
_client: mqtt.Client | None = None
_loop: asyncio.AbstractEventLoop | None = None
_vessels: dict[int, dict[str, Any]] = {}
_vessels_lock = threading.Lock()


def _handle_message(topic: str, payload: bytes) -> None:
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "vessels-v2" or not parts[1].isdigit():
        return
    mmsi = int(parts[1])
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return

    with _vessels_lock:
        vessel = _vessels.setdefault(mmsi, {"id": mmsi, "mmsi": mmsi, "source": "digitraffic"})
        if parts[2] == "locations":
            lat, lng = data.get("lat"), data.get("lon")
            if lat is None or lng is None:
                return
            if vessel.get("firstSeenLat") is None:
                vessel["firstSeenLat"], vessel["firstSeenLng"] = lat, lng
                vessel["firstSeenAt"] = data.get("time")
            vessel.update({
                "lat": lat,
                "lng": lng,
                "speed": data.get("sog"),
                "heading": data.get("heading") if data.get("heading") not in (None, 511) else data.get("cog"),
                "timestamp": data.get("time"),
            })
        elif parts[2] == "metadata":
            vessel.update({
                "name": (data.get("name") or "").strip() or None,
                "destination": (data.get("destination") or "").strip() or None,
                "type": "tanker" if 80 <= (data.get("type") or 0) <= 89 else "cargo",
            })
        else:
            return
        ship = vessel.copy()

    if ship.get("lat") is not None and _loop is not None:
        asyncio.run_coroutine_threadsafe(ais_stream.ingest_external_ship(mmsi, ship), _loop)


async def _run() -> None:
    global _client, _loop
    _loop = asyncio.get_running_loop()
    try:
        while True:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"ltmap-{uuid.uuid4().hex[:16]}",
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )
            _client = client
            client.ws_set_options(path=DIGITRAFFIC_MQTT_PATH)
            client.tls_set()

            def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
                if reason_code == 0:
                    client.subscribe("vessels-v2/#")
                    logger.info("Digitraffic AIS WebSocket connected (Finnish waters)")
                else:
                    logger.warning("Digitraffic AIS connection rejected: %s", reason_code)

            def on_disconnect(client: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
                logger.info("Digitraffic AIS WebSocket disconnected: %s", reason_code)

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.on_message = lambda _client, _userdata, message: _handle_message(message.topic, message.payload)
            try:
                client.connect(DIGITRAFFIC_MQTT_HOST, DIGITRAFFIC_MQTT_PORT, keepalive=60)
                client.loop_start()
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Digitraffic AIS WebSocket failed; retrying in 5 seconds")
            finally:
                client.loop_stop()
                try:
                    client.disconnect()
                except Exception:
                    pass
                _client = None
            await asyncio.sleep(5)
    finally:
        _loop = None


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
