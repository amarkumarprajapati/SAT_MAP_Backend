import os
import sys

# When running inside PyInstaller bundle, point to the executable directory
if getattr(sys, "frozen", False):
    bundle_dir = os.path.dirname(sys.executable)
    os.chdir(bundle_dir)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"[SAT Backend] Starting FastAPI on {settings.HOST}:{settings.PORT} (PID {os.getpid()})...")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False)
