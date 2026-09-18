from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.admin import router as admin_router, simulations
from app.api.agents import router as agents_router
from app.api.ws import router as ws_router
from app.game.archive import archive_game, env_flag


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
    # A reset is not the only way a game ends. Editing any .py file trips
    # uvicorn's reloader, which tears down this process and takes the whole
    # in-memory simulation with it -- including the box score, which exists
    # nowhere else. Without this hook the runs most worth keeping, the ones
    # interrupted by a change, would be exactly the ones lost.
    #
    # Costs a few seconds on shutdown for a long game, since the prompt log
    # runs to tens of megabytes. Set CAT_GAME_ARCHIVE_ON_SHUTDOWN=0 to skip it
    # while iterating rapidly on code that has nothing to do with the cat.
    if env_flag("CAT_GAME_ARCHIVE_ON_SHUTDOWN", default=True):
        for sim in list(simulations.values()):
            await archive_game(sim, reason="shutdown")



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
            "connect-src 'self' ws: wss:;"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Include API routers
app.include_router(admin_router)
app.include_router(agents_router)
app.include_router(ws_router)

# Mount static files
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🐱</text></svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/")
async def root():
    return FileResponse(static_dir / "index.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(static_dir / "settings.html")


@app.get("/boxscore")
async def boxscore_page():
    return FileResponse(static_dir / "boxscore.html")


