from contextlib import asynccontextmanager

# Học tập: hiểu cách khởi tạo ứng dụng FastAPI
# Học tập: nắm rõ vòng đời lifespan và routing
#
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from masterone.api.auth import router as auth_router
from masterone.api.large_imports import router as large_imports_router
from masterone.api.mdm_explanations import router as mdm_explanations_router, metrics_router as mdm_llm_metrics_router
from masterone.api.mdm_imports import router as mdm_imports_router
from masterone.api.mdm_reviews import router as mdm_reviews_router
from masterone.api.mdm_runs import router as mdm_runs_router
from masterone.api.mdm_resolution import router as mdm_resolution_router
from masterone.api.routes import router
from masterone.auth import models as auth_models  # noqa: F401 - registers auth tables
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm import models as mdm_models  # noqa: F401 - registers SQLAlchemy models
from masterone.mdm.migrate import require_current
from masterone.mdm.resolution_worker import ResolutionWorker
from masterone.storage import repository as storage_repository  # noqa: F401 - registers storage models


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.app_env == "production" and len(settings.auth_csrf_secret) < 32:
        raise RuntimeError("AUTH_CSRF_SECRET must be at least 32 characters in production")
    engine, factory = create_database(settings.mdm_database_url)
    require_current(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    worker = None
    if settings.app_env != "test":
        worker = ResolutionWorker(factory)
        worker.start()
        app.state.resolution_worker = worker
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    try:
        yield
    finally:
        if worker is not None:
            worker.close()
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
app.include_router(mdm_runs_router, prefix="/api/v1")
app.include_router(mdm_resolution_router, prefix="/api/v1")
app.include_router(mdm_explanations_router, prefix="/api/v1")
app.include_router(mdm_llm_metrics_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(large_imports_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
