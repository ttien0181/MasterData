from contextlib import asynccontextmanager

# Học tập: hiểu cách khởi tạo ứng dụng FastAPI
# Học tập: nắm rõ vòng đời lifespan và routing
#
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from masterone.api.auth import router as auth_router
from masterone.api.mdm_imports import router as mdm_imports_router
from masterone.api.mdm_reviews import router as mdm_reviews_router
from masterone.api.routes import router
from masterone.auth import models as auth_models  # noqa: F401 - registers auth tables
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm import models as mdm_models  # noqa: F401 - registers SQLAlchemy models
from masterone.mdm.migrate import require_current


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_env == "production" and len(settings.auth_csrf_secret) < 32:
        raise RuntimeError("AUTH_CSRF_SECRET must be at least 32 characters in production")
    engine, factory = create_database(settings.mdm_database_url)
    require_current(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    try:
        yield
    finally:
        engine.dispose()
    print("Shutting down...")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip().rstrip("/") for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(mdm_imports_router, prefix="/api/v1")
app.include_router(mdm_reviews_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
