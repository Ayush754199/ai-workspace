"""Runs the FastAPI app in-process with a detailed error handler so uncaught
500s from the live test suite are printed instead of being hidden in a
background terminal. Test-only helper: it does not modify the app."""
import os
import sys
import traceback

os.environ.setdefault("MONGODB_URL", "")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from app.main import app  # noqa: E402

LOG = os.path.join(os.path.dirname(__file__), "qa_logs", "qa_backend_trace.log")


@app.exception_handler(Exception)
async def dump_exception(request: Request, exc: Exception):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(f"\n=== {request.method} {request.url.path} -> {type(exc).__name__}: {exc}\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
