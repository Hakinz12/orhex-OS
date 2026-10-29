"""Main entrypoint for ORHEX OS FastAPI Service."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.rate_limit import SensitiveEndpointRateLimiter
from app.core.database import AsyncSessionLocal, init_db
from app.adapters.walrus.memwal import close_walrus_client
from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.memory import router as memory_router
from app.api.v1.memory import ErrorResponse
from app.services.memory_persistence import recover_pending_operations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("orhex.api")
_sensitive_rate_limiter = SensitiveEndpointRateLimiter(
    requests_per_minute=settings.AUTH_RATE_LIMIT_PER_MINUTE
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown routines."""
    logger.info("Initializing ORHEX OS database tables...")
    try:
        settings.validate_security_configuration()
        await init_db()
        async with AsyncSessionLocal() as session:
            recovered = await recover_pending_operations(session)
        logger.info("Marked %s stale memory operation(s) for reconciliation.", recovered)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error("Database initialization failed: %s", type(e).__name__)
        raise
    yield
    logger.info("Closing Walrus Memory client...")
    await close_walrus_client()
    logger.info("ORHEX OS API shutting down.")


app = FastAPI(
    title=settings.APP_NAME,
    description="Your World. Understood. — Memory-Powered Personal Operating System",
    version="0.1.0",
    lifespan=lifespan
)


@app.middleware("http")
async def limit_memory_api_requests(request: Request, call_next):
    if (
        request.url.path.startswith("/api/v1/memory/")
        or request.url.path == "/api/v1/auth/session"
    ):
        peer = request.client.host if request.client is not None else "unknown"
        allowed, retry_after = await _sensitive_rate_limiter.check(peer)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": "Too many requests. Retry after the indicated interval.",
                    "operation_id": None,
                },
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def handle_http_error(_request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail.get("code", "http_error")
        message = detail.get("message", "The request could not be completed.")
        operation_id = detail.get("operation_id")
    else:
        error = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
            503: "service_unavailable",
        }.get(exc.status_code, "http_error")
        message = str(detail)
        operation_id = None
    payload = ErrorResponse(
        error=error,
        message=message,
        operation_id=operation_id,
    )
    headers = exc.headers or {}
    return JSONResponse(
        status_code=exc.status_code,
        content=payload.model_dump(),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation(_request: Request, _exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="invalid_request",
            message="Request validation failed.",
        ).model_dump(),
    )


# Configure CORS for Next.js frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(memory_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root info endpoint."""
    return {
        "app": settings.APP_NAME,
        "tagline": "Your World. Understood.",
        "version": "0.1.0",
        "health_check": "/api/v1/health",
        "documentation": "/docs"
    }
