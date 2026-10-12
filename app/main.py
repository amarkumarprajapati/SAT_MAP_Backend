import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

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


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RobustCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        
        if request.method == "OPTIONS":
            res = Response(status_code=204)
            if origin:
                res.headers["Access-Control-Allow-Origin"] = origin
                res.headers["Access-Control-Allow-Credentials"] = "true"
            else:
                res.headers["Access-Control-Allow-Origin"] = "*"
            res.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD"
            res.headers["Access-Control-Allow-Headers"] = request.headers.get("access-control-request-headers", "*")
            res.headers["Access-Control-Allow-Private-Network"] = "true"
            res.headers["Access-Control-Max-Age"] = "86400"
            return res

        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response


app.add_middleware(RobustCORSMiddleware)

start_time = time.time()


@app.get(Endpoints.ROOT)
@app.get(Endpoints.HEALTH)
@app.get(Endpoints.API_HEALTH)
async def health_check():

    return {
        "status": "connected",
        "service": "LTmap FastAPI Backend",
        "version": "1.0.0",
        "base_url": settings.BASE_URL,
        "api_prefix": settings.API_PREFIX,
        "uptime_seconds": round(time.time() - start_time, 2),
        "timestamp": int(time.time() * 1000),
        "endpoints": {
            "health": Endpoints.API_HEALTH,
            "flights": Endpoints.FLIGHTS,
            "maritime": Endpoints.MARITIME,
            "satellites": Endpoints.SATELLITES,
            "satellite_orbit": Endpoints.SATELLITES_ORBIT,
            "region_dossier": Endpoints.REGION_DOSSIER,
            "stats": Endpoints.STATS,
        },
    }



app.include_router(satellites.router)
app.include_router(flights.router)
app.include_router(maritime.router)
app.include_router(region_dossier.router)
app.include_router(stats.router)
