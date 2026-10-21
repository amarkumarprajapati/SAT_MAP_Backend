from .config import settings

BASE_URL = settings.BASE_URL
API_PREFIX = settings.API_PREFIX


class Endpoints:
    ROOT = "/"
    HEALTH = "/health"
    API_HEALTH = f"{API_PREFIX}/health"
    FLIGHTS = f"{API_PREFIX}/flights"
    MARITIME = f"{API_PREFIX}/maritime"
    SATELLITES = f"{API_PREFIX}/satellites"
    SATELLITES_ORBIT = f"{API_PREFIX}/satellites/orbit"
    REGION_DOSSIER = f"{API_PREFIX}/region-dossier"
    STATS = f"{API_PREFIX}/stats"
    NEWS = f"{API_PREFIX}/news"

    @classmethod
    def url(cls, endpoint: str) -> str:
        """Return the absolute URL for a given endpoint path."""
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{BASE_URL.rstrip('/')}{path}"


endpoints = Endpoints()
