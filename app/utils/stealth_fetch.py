"""
LTmap — Stealth HTTP Client

Generates randomized HTTP headers to distribute API requests across a pool
of spoofed residential IP addresses and browser fingerprints. This helps
avoid rate-limiting on open ADS-B and satellite data sources.
"""

import random
import httpx

# Residential IP ranges (common ISP subnets globally)
_IP_POOLS = [
    # US Comcast
    {"base": [73, 15], "range": [255, 255]},
    {"base": [98, 24], "range": [255, 255]},
    {"base": [174, 51], "range": [255, 255]},
    # US AT&T
    {"base": [107, 77], "range": [255, 255]},
    {"base": [166, 198], "range": [255, 255]},
    # US Verizon
    {"base": [71, 172], "range": [255, 255]},
    {"base": [100, 0], "range": [127, 255]},
    # UK BT
    {"base": [86, 128], "range": [127, 255]},
    {"base": [81, 132], "range": [63, 255]},
    # DE Telekom
    {"base": [91, 64], "range": [63, 255]},
    {"base": [80, 128], "range": [63, 255]},
    # FR Orange
    {"base": [90, 0], "range": [63, 255]},
    {"base": [86, 192], "range": [63, 255]},
    # IT Telecom Italia
    {"base": [79, 0], "range": [63, 255]},
    {"base": [87, 0], "range": [31, 255]},
    # BR Vivo
    {"base": [177, 0], "range": [127, 255]},
    # AU Telstra
    {"base": [101, 160], "range": [31, 255]},
    # IN Jio
    {"base": [49, 32], "range": [31, 255]},
    # CA Rogers
    {"base": [99, 224], "range": [31, 255]},
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]


def _generate_residential_ip() -> str:
    pool = random.choice(_IP_POOLS)
    octet3 = pool["base"][1] + random.randint(0, pool["range"][0])
    octet4 = random.randint(1, pool["range"][1])
    return f'{pool["base"][0]}.{octet3}.{octet4 or 1}.{random.randint(1, 254)}'


def stealth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Generate spoofed headers for a stealth fetch request."""
    ip = _generate_residential_ip()
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "X-Forwarded-For": ip,
        "X-Real-IP": ip,
    }
    if extra:
        headers.update(extra)
    return headers


async def stealth_get(url: str, timeout: float = 30.0, extra_headers: dict | None = None) -> httpx.Response:
    """Perform an HTTP GET with stealth headers. Drop-in for httpx.get()."""
    headers = stealth_headers(extra_headers)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url, headers=headers)


async def stealth_get_json(url: str, timeout: float = 30.0, extra_headers: dict | None = None) -> dict | list | None:
    """Stealth GET that returns parsed JSON, or None on failure."""
    try:
        resp = await stealth_get(url, timeout=timeout, extra_headers=extra_headers)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None
