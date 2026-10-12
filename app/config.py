import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel

# Load .env from backend root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings(BaseModel):
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    BASE_URL: str = os.getenv("BASE_URL", f"http://localhost:{os.getenv('PORT', '8000')}")
    API_PREFIX: str = os.getenv("API_PREFIX", "/api")
    TIMEOUT_SECONDS: float = float(os.getenv("TIMEOUT_SECONDS", "30.0"))
    AIS_API_KEY: str = os.getenv("AIS_API_KEY", "")
    OPENSKY_CLIENT_ID: str = os.getenv("OPENSKY_CLIENT_ID", "")
    OPENSKY_CLIENT_SECRET: str = os.getenv("OPENSKY_CLIENT_SECRET", "")
    CACHE_DIR: str = os.getenv("CACHE_DIR", str(Path(__file__).resolve().parent.parent / ".cache"))

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "*",
    ]


settings = Settings()

# Ensure cache directory exists
Path(settings.CACHE_DIR).mkdir(parents=True, exist_ok=True)
