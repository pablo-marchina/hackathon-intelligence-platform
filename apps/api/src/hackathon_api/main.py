from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from hackathon_shared.logging import configure_logging
from hackathon_shared.settings import get_settings
from hackathon_api.routes.admin import router as admin_router
from hackathon_api.routes.hackathons import router as hackathons_router
from hackathon_api.routes.catalog import router as catalog_router
from hackathon_api.routes.search import router as search_router
from hackathon_api.routes.auth import router as auth_router
from hackathon_api.routes.users import router as users_router
from hackathon_api.routes.personalization import router as personalization_router
from hackathon_api.body_limit import RequestBodyLimitMiddleware
from hackathon_api.db import get_engine

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

app = FastAPI(
    title="Hackathon Intelligence API",
    version="1.0.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
v1 = APIRouter(prefix="/v1")
v1.include_router(hackathons_router)
v1.include_router(catalog_router)
v1.include_router(search_router)
v1.include_router(auth_router)
v1.include_router(users_router)
v1.include_router(personalization_router)
app.include_router(v1)
app.include_router(admin_router)
# Order is significant (last registered runs first): reject bad hosts before
# processing request bodies, but let CORS annotate early 413/400 responses.
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", supplied_id) else str(uuid4())
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_context.reset(token)
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["referrer-policy"] = "no-referrer"
    response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.app_env == "production":
        response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


# Liveness intentionally does not depend on external infrastructure. Readiness
# is stricter: do not send requests to an instance with a missing/outdated schema.
EXPECTED_DB_REVISION = "0009_w6_alert_match"


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            revision = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("readiness database probe failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="service not ready") from exc
    if revision != EXPECTED_DB_REVISION:
        raise HTTPException(status_code=503, detail="service not ready")
    return {"status": "ready"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_exception",
        extra={
            "request_id": request_id_context.get(),
            "path": request.url.path,
            "method": request.method,
        },
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
