import sys
import uvicorn
from app.config import settings

if __name__ == "__main__":
    reload = "--reload" in sys.argv or "-r" in sys.argv
    print(f"[LTmap] Starting FastAPI Backend on {settings.HOST}:{settings.PORT} (reload={reload})...")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=reload)
