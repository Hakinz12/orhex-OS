"""Main entrypoint for ORHREX OS FastAPI Service."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.api.v1.health import router as health_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("orhrex.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown routines."""
    logger.info("Initializing ORHREX OS database tables...")
    try:
        await init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    yield
    logger.info("ORHREX OS API shutting down.")


app = FastAPI(
    title=settings.APP_NAME,
    description="Intelligence That Anticipates — Memory-Powered Personal AI Operating System",
    version="0.1.0",
    lifespan=lifespan
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


@app.get("/")
async def root():
    """Root info endpoint."""
    return {
        "app": settings.APP_NAME,
        "tagline": "Intelligence That Anticipates",
        "version": "0.1.0",
        "health_check": "/api/v1/health",
        "documentation": "/docs"
    }

