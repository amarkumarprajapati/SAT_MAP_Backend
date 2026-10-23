import time
import asyncio
import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ltmap.main")

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from .config import settings
from . import database
from .endpoints import Endpoints
from .routers import satellites, flights, maritime, stats, region_dossier, providers, osint, geospatial_video
from .services import ais_stream, digitraffic_ais, tle_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    await providers.open_client()
    await osint.open_client()
    await tle_cache.restore_database_cache()
    tle_refresh_task = None
    try:
        await ais_stream.start()
        await digitraffic_ais.start()
        tle_refresh_task = asyncio.create_task(tle_cache.refresh(), name="tle-refresh")
        yield
    finally:
        if tle_refresh_task is not None:
            tle_refresh_task.cancel()
            try:
                await tle_refresh_task
            except asyncio.CancelledError:
                pass
        await ais_stream.stop()
        await digitraffic_ais.stop()
        await providers.close_client()
        await osint.close_client()
        await database.close()


app = FastAPI(
    title="LTmap Intelligence Backend",
    description="High-performance FastAPI service powering the LTmap Geospatial & OSINT Dashboard.",
    version="1.0.0",
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.APP_ENV != "production" else None,
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
    database_status = await database.status()
    healthy = not settings.DATABASE_REQUIRED or database_status == "connected"
    return JSONResponse(status_code=200 if healthy else 503, content={
        "status": "connected" if healthy else "unavailable",
        "service": "LTmap FastAPI Backend",
        "version": "1.0.0",
        "base_url": settings.BASE_URL,
        "database": database_status,
    })



app.include_router(satellites.router)
app.include_router(flights.router)
app.include_router(maritime.router)
app.include_router(region_dossier.router)
app.include_router(stats.router)
app.include_router(providers.router)
app.include_router(osint.router)
app.include_router(geospatial_video.router)

app.add_middleware(
    PrivateNetworkCORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
        if settings.APP_ENV != "production"
        else None
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)
