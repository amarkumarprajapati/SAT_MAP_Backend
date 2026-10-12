"""
LTmap — SGP4/SDP4 Orbital Propagation

Python port of the frontend's orbit.ts. Uses the sgp4 library (Python implementation
of the same SGP4/SDP4 algorithms that satellite.js provides in JavaScript).

Provides:
- propagate_tle()       — current position from a TLE
- propagate_telemetry() — position + speed + heading
- orbit_path()          — full revolution of orbit track points
- split_at_antimeridian() — split path at ±180° for clean map rendering
- orbital_period_minutes() — period from mean motion
"""

import math
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec, jday

# ── WGS84 Ellipsoid Constants ──────────────────────────────────────────────────
WGS84_A = 6378.137            # semi-major axis (km)
WGS84_F = 1.0 / 298.257223563  # flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_E2 = 1.0 - (WGS84_B / WGS84_A) ** 2


def _jday_from_dt(dt: datetime) -> tuple[float, float]:
    """Convert a datetime to a (jd, fr) Julian date pair for sgp4."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                dt.second + dt.microsecond / 1_000_000.0)


def _gmst(jd: float, fr: float = 0.0) -> float:
    """Greenwich Mean Sidereal Time in radians (IAU 1982 model)."""
    t = (jd - 2451545.0 + fr) / 36525.0
    # theta in seconds of time
    theta = (67310.54841
             + (876600.0 * 3600.0 + 8640184.812866) * t
             + 0.093104 * t * t
             - 6.2e-6 * t * t * t)
    # Convert seconds of time → degrees → radians, wrapped to [0, 2π)
    return math.radians((theta % 86400.0) / 240.0) % (2.0 * math.pi)


def _eci_to_geodetic(x: float, y: float, z: float, gmst_rad: float) -> tuple[float, float, float]:
    """
    Convert TEME (True Equator Mean Equinox) position to geodetic coordinates.
    Returns (lat_deg, lon_deg, alt_km) on the WGS84 ellipsoid.
    """
    # Longitude: subtract Earth's rotation angle
    lon = math.atan2(y, x) - gmst_rad
    lon = ((lon + math.pi) % (2.0 * math.pi)) - math.pi  # wrap to [-π, π]

    rho = math.sqrt(x * x + y * y)

    # Iterative latitude (converges in ~4 iterations for WGS84)
    lat = math.atan2(z, rho)
    for _ in range(10):
        sin_lat = math.sin(lat)
        N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        lat = math.atan2(z + WGS84_E2 * N * sin_lat, rho)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    N = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)

    if abs(cos_lat) > 1e-10:
        alt = rho / cos_lat - N
    else:
        alt = abs(z) - WGS84_B

    return math.degrees(lat), math.degrees(lon), alt


def _wrap_longitude(deg: float) -> float:
    """Wrap longitude to [-180, 180]."""
    return ((deg + 180.0) % 360.0 + 360.0) % 360.0 - 180.0


# ── Public API ──────────────────────────────────────────────────────────────────

def propagate_tle(line1: str, line2: str, when: datetime | None = None) -> dict | None:
    """
    Propagate a TLE to a point in time via SGP4/SDP4.

    Returns {"lat", "lng", "alt_km"} or None on failure (decayed, bad TLE, etc.).
    """
    if when is None:
        when = datetime.now(timezone.utc)

    try:
        sat = Satrec.twoline2rv(line1, line2)
    except Exception:
        return None
    if hasattr(sat, "error") and sat.error:
        return None

    jd, fr = _jday_from_dt(when)
    e, r, v = sat.sgp4(jd, fr)
    if e != 0 or r is None:
        return None

    x, y, z = r
    if not all(math.isfinite(c) for c in (x, y, z)):
        return None

    g = _gmst(jd, fr)
    lat, lng, alt_km = _eci_to_geodetic(x, y, z, g)
    lng = _wrap_longitude(lng)

    if not all(math.isfinite(v) for v in (lat, lng, alt_km)):
        return None
    if alt_km < 80 or alt_km > 60000:
        return None

    return {
        "lat": round(lat, 4),
        "lng": round(lng, 4),
        "alt_km": round(alt_km),
    }


def propagate_telemetry(line1: str, line2: str, when: datetime | None = None) -> dict | None:
    """
    Propagate position + compute inertial speed and ground-track heading.

    Returns {"lat", "lng", "alt_km", "speedKmS", "headingDeg"} or None.
    """
    if when is None:
        when = datetime.now(timezone.utc)

    pos = propagate_tle(line1, line2, when)
    if not pos:
        return None

    # Speed from velocity vector
    speed_km_s = None
    try:
        sat = Satrec.twoline2rv(line1, line2)
        jd, fr = _jday_from_dt(when)
        e, _r, v = sat.sgp4(jd, fr)
        if e == 0 and v is not None:
            vx, vy, vz = v
            speed_km_s = math.sqrt(vx * vx + vy * vy + vz * vz)
    except Exception:
        pass

    # Fallback: circular orbit estimate
    if speed_km_s is None or not math.isfinite(speed_km_s):
        period = orbital_period_minutes(line2)
        if period:
            speed_km_s = (2.0 * math.pi * (6371.0 + pos["alt_km"])) / (period * 60.0)
        else:
            return None

    # Heading from short forward propagation (30s)
    next_pos = propagate_tle(line1, line2, when + timedelta(seconds=30))
    if not next_pos:
        return None

    lat1 = math.radians(pos["lat"])
    lat2 = math.radians(next_pos["lat"])
    d_lng = math.radians(next_pos["lng"] - pos["lng"])

    y_val = math.sin(d_lng) * math.cos(lat2)
    x_val = (math.cos(lat1) * math.sin(lat2)
             - math.sin(lat1) * math.cos(lat2) * math.cos(d_lng))

    angular_change = math.sqrt(
        (next_pos["lat"] - pos["lat"]) ** 2
        + ((next_pos["lng"] - pos["lng"]) * math.cos(lat1)) ** 2
    )

    period = orbital_period_minutes(line2)
    # Geostationary satellites barely move; show eastward direction
    if angular_change < 0.001 and period and period > 1000:
        heading_deg = 90.0
    else:
        heading_deg = (math.degrees(math.atan2(y_val, x_val)) + 360.0) % 360.0

    return {
        **pos,
        "speedKmS": round(speed_km_s, 4),
        "headingDeg": round(heading_deg, 1),
    }


def orbital_period_minutes(line2: str) -> float | None:
    """Orbital period from the TLE's mean motion (revolutions/day)."""
    try:
        mean_motion = float(line2[52:63])
        if mean_motion <= 0 or not math.isfinite(mean_motion):
            return None
        return 1440.0 / mean_motion
    except (ValueError, IndexError):
        return None


def orbit_path(
    line1: str, line2: str, steps: int = 180, from_dt: datetime | None = None
) -> list[dict]:
    """
    Sample one full revolution, returning a list of {lat, lng, alt_km} dicts.

    Sampled in time so the ground track carries Earth's rotation naturally.
    """
    if from_dt is None:
        from_dt = datetime.now(timezone.utc)

    period = orbital_period_minutes(line2)
    if not period:
        return []

    points = []
    for i in range(steps + 1):
        when = from_dt + timedelta(minutes=period * i / steps)
        p = propagate_tle(line1, line2, when)
        if p:
            points.append(p)
    return points


def split_at_antimeridian(path: list[dict]) -> list[list[dict]]:
    """
    Split a path wherever it crosses the antimeridian (±180°).

    Each returned run can be drawn as one continuous LineString without the
    line sweeping back across the world.
    """
    if not path:
        return []

    runs: list[list[dict]] = []
    run = [path[0]]

    for i in range(1, len(path)):
        if abs(path[i]["lng"] - path[i - 1]["lng"]) > 180:
            runs.append(run)
            run = []
        run.append(path[i])

    if run:
        runs.append(run)
    return [r for r in runs if len(r) > 1]
