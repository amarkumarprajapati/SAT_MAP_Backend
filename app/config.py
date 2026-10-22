import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings(BaseModel):
    APP_ENV: str = os.getenv("APP_ENV", "development").lower()
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    BASE_URL: str = os.getenv("BASE_URL", f"http://localhost:{os.getenv('PORT', '8000')}")
    API_PREFIX: str = os.getenv("API_PREFIX", "/api")
    TIMEOUT_SECONDS: float = float(os.getenv("TIMEOUT_SECONDS", "30.0"))
    AIS_API_KEY: str = os.getenv("AIS_API_KEY", "")
    OPENSKY_CLIENT_ID: str = os.getenv("OPENSKY_CLIENT_ID", "")
    OPENSKY_CLIENT_SECRET: str = os.getenv("OPENSKY_CLIENT_SECRET", "")
    OSINT_SERVICE_URL: str = os.getenv("OSINT_SERVICE_URL", "http://127.0.0.1:5000").rstrip("/")
    CACHE_DIR: str = os.getenv("CACHE_DIR", str(Path(__file__).resolve().parent.parent / ".cache"))
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    DATABASE_REQUIRED: bool = os.getenv("DATABASE_REQUIRED", "false").lower() in ("1", "true", "yes")
    DATABASE_POOL_SIZE: int = max(1, int(os.getenv("DATABASE_POOL_SIZE", "5")))
    DATABASE_COMMAND_TIMEOUT: float = float(os.getenv("DATABASE_COMMAND_TIMEOUT", "10"))

    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]


settings = Settings()

if settings.APP_ENV == "production":
    if not settings.DATABASE_URL or not settings.DATABASE_REQUIRED:
        raise RuntimeError("Production requires DATABASE_URL and DATABASE_REQUIRED=true")
    if not settings.CORS_ORIGINS or "*" in settings.CORS_ORIGINS:
        raise RuntimeError("Production requires explicit CORS_ORIGINS; wildcard origins are not allowed")


Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)
