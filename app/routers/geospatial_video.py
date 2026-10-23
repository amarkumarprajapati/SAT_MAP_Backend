"""Normalize time-synchronized geospatial-video CSV metadata for the map UI."""

import csv
import io
import math
import re
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(prefix="/api/geospatial-video", tags=["geospatial-video"])
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_METADATA_ROWS = 30_000


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _number(row: dict[str, str], aliases: tuple[str, ...]) -> float | None:
    keys = {_key(key): value for key, value in row.items() if key is not None}
    for alias in aliases:
        raw = keys.get(_key(alias), "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
            if math.isfinite(value):
                return value
        except ValueError:
            continue
    return None


def _first_number(row: dict[str, str], aliases: tuple[str, ...]) -> float | None:
    return _number(row, aliases)


def _timestamp(row: dict[str, str]) -> tuple[float | None, bool]:
    elapsed = _first_number(row, ("ElapsedTime", "ElapsedSeconds", "VideoTime", "FrameTime"))
    if elapsed is not None:
        return elapsed, True
    value = _first_number(row, ("PrecisionTimestamp", "UnixTimeStamp", "Timestamp", "TimeStamp", "UTC"))
    if value is None:
        return None, False
    # MISB PrecisionTimestamp is microseconds; common Unix timestamps may be
    # milliseconds or seconds. Normalization happens after all rows are read.
    if value > 1e14:
        return value / 1e6, False
    if value > 1e11:
        return value / 1e3, False
    return value, False


def _frame_polygon(row: dict[str, str], lat: float, lng: float, altitude_m: float | None) -> list[list[list[float]]] | None:
    corners: list[list[float]] = []
    for index in range(1, 5):
        corner_lat = _number(row, (f"CornerLat{index}", f"CornerLatitude{index}", f"Corner{index}Latitude"))
        corner_lng = _number(row, (f"CornerLon{index}", f"CornerLng{index}", f"CornerLongitude{index}", f"Corner{index}Longitude"))
        if corner_lat is None or corner_lng is None:
            corners = []
            break
        corners.append([corner_lng, corner_lat])
    if len(corners) == 4:
        corners.append(corners[0])
        return [corners]

    # For CSVs with the core camera metadata but no precomputed corner fields,
    # derive a ground footprint using altitude, pointing, and horizontal/vertical FOV.
    hfov = _number(row, ("SensorHorizontalFieldOfView", "HorizontalFieldOfView", "HFOV"))
    vfov = _number(row, ("SensorVerticalFieldOfView", "VerticalFieldOfView", "VFOV"))
    elevation = _number(row, ("SensorRelativeElevationAngle", "SensorElevationAngle"))
    if altitude_m is None or altitude_m <= 0 or hfov is None or vfov is None or elevation is None:
        return None
    if not (0 < hfov < 179 and 0 < vfov < 179):
        return None

    # Relative elevation is measured from the horizon in standard FMV CSVs.
    depression = abs(elevation)
    if depression <= 1 or depression > 90:
        return None
    center_distance = altitude_m / math.tan(math.radians(depression))
    heading = _number(row, ("PlatformHeadingAngle", "Heading")) or 0.0
    azimuth = _number(row, ("SensorRelativeAzimuthAngle",)) or 0.0
    bearing = math.radians((heading + azimuth) % 360)
    center_lat = lat + (center_distance * math.cos(bearing)) / 111_320
    cos_lat = max(0.05, math.cos(math.radians(lat)))
    center_lng = lng + (center_distance * math.sin(bearing)) / (111_320 * cos_lat)
    width = 2 * altitude_m * math.tan(math.radians(hfov / 2))
    height = 2 * altitude_m * math.tan(math.radians(vfov / 2))
    heading_rad = math.radians(heading)
    ring: list[list[float]] = []
    for x, y in ((-width / 2, -height / 2), (width / 2, -height / 2), (width / 2, height / 2), (-width / 2, height / 2)):
        east = x * math.cos(heading_rad) + y * math.sin(heading_rad)
        north = -x * math.sin(heading_rad) + y * math.cos(heading_rad)
        ring.append([center_lng + east / (111_320 * cos_lat), center_lat + north / 111_320])
    ring.append(ring[0])
    return [ring]


@router.post("/metadata")
async def parse_video_metadata(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload the geospatial video's synchronized metadata as a CSV file.")
    payload = await file.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise HTTPException(status_code=413, detail="Metadata CSV exceeds the 8 MB limit.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Metadata CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Metadata CSV is empty or missing its header row.")

    parsed: list[tuple[float | None, bool, dict[str, str]]] = []
    for row in reader:
        if len(parsed) >= MAX_METADATA_ROWS:
            break
        stamp, is_elapsed = _timestamp(row)
        lat = _number(row, ("SensorLatitude", "Latitude", "Lat"))
        lng = _number(row, ("SensorLongitude", "Longitude", "Lon", "Lng"))
        if stamp is None or lat is None or lng is None or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            continue
        parsed.append((stamp, is_elapsed, row))
    if not parsed:
        raise HTTPException(status_code=422, detail="No usable rows. Include a timestamp, SensorLatitude, and SensorLongitude for each frame.")

    first_stamp = parsed[0][0] or 0.0
    frames: list[dict[str, Any]] = []
    for stamp, is_elapsed, row in parsed:
        lat = _number(row, ("SensorLatitude", "Latitude", "Lat"))
        lng = _number(row, ("SensorLongitude", "Longitude", "Lon", "Lng"))
        assert lat is not None and lng is not None and stamp is not None
        time_seconds = stamp if is_elapsed else max(0.0, stamp - first_stamp)
        altitude = _number(row, ("SensorTrueAltitude", "SensorEllipsoidHeight", "Altitude", "Alt", "Height"))
        # MISB altitude fields are meters; tolerate explicit kilometre columns.
        if _number(row, ("AltitudeKm", "AltKm")) is not None:
            altitude = _number(row, ("AltitudeKm", "AltKm")) * 1000  # type: ignore[operator]
        frame_lat = _number(row, ("FrameCenterLatitude", "FrameLatitude"))
        frame_lng = _number(row, ("FrameCenterLongitude", "FrameLongitude"))
        polygon = _frame_polygon(row, frame_lat if frame_lat is not None else lat, frame_lng if frame_lng is not None else lng, altitude)
        frames.append({
            "timeSeconds": round(max(0.0, time_seconds), 4),
            "sensor": {"lat": lat, "lng": lng, "altitudeM": altitude},
            "center": {"lat": frame_lat, "lng": frame_lng} if frame_lat is not None and frame_lng is not None else None,
            "headingDeg": _number(row, ("PlatformHeadingAngle", "Heading")),
            "pitchDeg": _number(row, ("PlatformPitchAngle", "Pitch")),
            "footprint": polygon,
        })
    frames.sort(key=lambda frame: frame["timeSeconds"])
    return {"frames": frames, "count": len(frames), "footprintFrames": sum(frame["footprint"] is not None for frame in frames)}
