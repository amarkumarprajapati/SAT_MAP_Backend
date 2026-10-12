import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .config import settings
from .endpoints import Endpoints
from .routers import satellites, flights, maritime, stats, region_dossier
from .services import ais_stream, tle_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AIS_API_KEY:
        await ais_stream.start()
    asyncio.create_task(tle_cache.refresh())
    yield
    await ais_stream.stop()


app = FastAPI(
    title="LTmap Intelligence Backend",
    description="High-performance FastAPI service powering the LTmap Geospatial & OSINT Dashboard.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


class PrivateNetworkCORSMiddleware(CORSMiddleware):
    """Standard CORS handling with Chrome's localhost PNA preflight support."""

    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if request_headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

start_time = time.time()


@app.get(Endpoints.ROOT)
@app.get(Endpoints.HEALTH)
@app.get(Endpoints.API_HEALTH)
async def health_check():

    return {
        "status": "connected",
        "service": "LTmap FastAPI Backend",
        "version": "1.0.0",
        "base_url": settings.BASE_URL
    }



app.include_router(satellites.router)
app.include_router(flights.router)
app.include_router(maritime.router)
app.include_router(region_dossier.router)
app.include_router(stats.router)


app = PrivateNetworkCORSMiddleware(
    app,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)
