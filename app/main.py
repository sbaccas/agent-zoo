from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.admin import router as admin_router
from app.api.agents import router as agents_router


class PollingLogFilter(logging.Filter):
    """Filter out noisy 200 OK access logs from high-frequency polling endpoints."""
    def filter(self, record: logging.LogRecord) -> bool:
        args = getattr(record, "args", ())
        if len(args) >= 5:
            # args format: (client_addr, method, path, http_version, status_code)
            method = args[1]
            path = args[2]
            status = args[4]
            if method == "GET" and status == 200 and any(p in path for p in ["/state", "/observation", "/agents"]):
                return False
        msg = record.getMessage()
        if "GET /api/simulations/" in msg and (" 200 " in msg or "200 OK" in msg) and any(p in msg for p in ["/state", "/observation", "/agents"]):
            return False
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Suppress high-frequency polling from uvicorn.access logger and handlers
    filt = PollingLogFilter()
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.addFilter(filt)
    for h in access_logger.handlers:
        h.addFilter(filt)
    yield


app = FastAPI(
    title="Cat & Mouse Simulation Server",
    version="0.1.0",
    description="Multi-agent simulation environment for autonomous AI agents",
    lifespan=lifespan,
)

# Also apply filter immediately at module load time
_filt = PollingLogFilter()
_acc_log = logging.getLogger("uvicorn.access")
_acc_log.addFilter(_filt)
for _h in _acc_log.handlers:
    _h.addFilter(_filt)



class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Include API routers
app.include_router(admin_router)
app.include_router(agents_router)

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/")
async def root():
    return FileResponse(static_dir / "index.html")
