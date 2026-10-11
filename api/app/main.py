import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.crypto import master_key_ready
from app.db import create_pool, run_migrations
from app.routers import ask, auth, git_settings, projects

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("askbox")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    await run_migrations(app.state.pool)
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    if not master_key_ready():
        log.warning("ASKBOX_MASTER_KEY ausente o inválida: el alta de claves devolverá 503")
    yield
    await app.state.redis.aclose()
    await app.state.pool.close()


app = FastAPI(title="askbox API", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(ask.router)
app.include_router(git_settings.router)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = [".".join(str(p) for p in e["loc"] if p != "body") for e in exc.errors()]
    return JSONResponse(
        {"error": {"code": "validation_error", "message": "Datos no válidos: " + ", ".join(fields)}}, status_code=422
    )


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.error("error no controlado: %s", type(exc).__name__)
    return JSONResponse({"error": {"code": "internal_error", "message": "Error interno."}}, status_code=500)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/ready")
async def ready(request: Request):
    checks = {}
    try:
        await request.app.state.pool.fetchval("SELECT 1")
        checks["db"] = True
    except Exception:  # noqa: BLE001
        checks["db"] = False
    try:
        await request.app.state.redis.ping()
        checks["redis"] = True
    except Exception:  # noqa: BLE001
        checks["redis"] = False
    checks["master_key"] = master_key_ready()
    return JSONResponse({"ok": all(checks.values()), **checks}, status_code=200 if all(checks.values()) else 503)
