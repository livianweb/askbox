import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from arq import create_pool as arq_create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.config import settings
from app.crypto import master_key_ready
from app.db import create_pool, run_migrations
from app.routers import access_tokens, alerts, ask, auth, batch, deploy_settings, git_settings, projects, prompts, usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("askbox")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    await run_migrations(app.state.pool)
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.arq = await arq_create_pool(RedisSettings.from_dsn(settings.redis_url))
    if not master_key_ready():
        log.warning("ASKBOX_MASTER_KEY ausente o inválida: el alta de claves devolverá 503")
    yield
    await app.state.arq.close()
    await app.state.redis.aclose()
    await app.state.pool.close()


def _public_openapi_url() -> str:
    prefix = settings.api_external_prefix
    return f"{prefix}/openapi.json" if prefix else "/openapi.json"


app = FastAPI(title="askbox API", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(access_tokens.router)
app.include_router(prompts.router)
app.include_router(ask.router)
app.include_router(usage.router)
app.include_router(batch.router)
app.include_router(alerts.router)
app.include_router(git_settings.router)
app.include_router(deploy_settings.router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version="0.1.0", routes=app.routes)
    if settings.api_external_prefix:
        schema["servers"] = [{"url": settings.api_external_prefix}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(openapi_url=_public_openapi_url(), title=f"{app.title} — Swagger")


@app.get("/redoc", include_in_schema=False)
async def redoc_ui():
    return get_redoc_html(openapi_url=_public_openapi_url(), title=f"{app.title} — ReDoc")


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
