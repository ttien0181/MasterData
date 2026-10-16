from contextlib import asynccontextmanager

# Học tập: hiểu cách khởi tạo ứng dụng FastAPI
# Học tập: nắm rõ vòng đời lifespan và routing
#
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from masterone.api.auth import router as auth_router
from masterone.api.mdm_resolution import router as mdm_resolution_router
from masterone.api.mdm_resolution import ws_router as mdm_resolution_ws_router
from masterone.api.mdm_runs import router as mdm_runs_router
from masterone.api.mdm_matching_foundation import router as matching_foundation_router
from masterone.api.mdm_uploads import router as mdm_uploads_router
from masterone.api.mdm_workflow import router as workflow_router
from masterone.api.routes import router
from masterone.auth import models as auth_models  # noqa: F401 - registers auth tables
from masterone.config import LOCAL_DEV_ORIGIN_REGEX, get_settings
from masterone.db.session import create_database
from masterone.mdm import models as mdm_models  # noqa: F401 - registers SQLAlchemy models
from masterone.mdm.input_schema import InputMappingError
from masterone.mdm.migrate import require_current, validate_current


@asynccontextmanager
async def lifespan(app: FastAPI):
    from masterone.logging_config import configure_logging
    configure_logging()
    settings = get_settings()
    if settings.app_env == "production" and len(settings.auth_csrf_secret) < 32:
        raise RuntimeError("AUTH_CSRF_SECRET must be at least 32 characters in production")
    from masterone.llm.policy import validate_startup
    validate_startup(settings)
    engine, factory = create_database(settings.active_mdm_database_url)
    try:
        if settings.app_env == "production":
            validate_current(engine)
        else:
            require_current(engine)
    except BaseException:
        engine.dispose()
        raise
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
    description="MasterOne record matching and human review",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()


@app.exception_handler(InputMappingError)
async def input_mapping_error_handler(_request, exc: InputMappingError):
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail, "error_report": exc.report})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip().rstrip("/") for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX if settings.app_env == "development" else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(mdm_runs_router, prefix="/api/v1")
app.include_router(matching_foundation_router, prefix="/api/v1")
app.include_router(workflow_router, prefix="/api/v1")
app.include_router(mdm_uploads_router, prefix="/api/v1")
app.include_router(mdm_resolution_router, prefix="/api/v1")
app.include_router(mdm_resolution_ws_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get("/ready")
def readiness():
    """Dependency readiness, separate from process liveness; safe error codes."""
    import boto3
    from botocore.config import Config

    checks = {"database": "unavailable", "storage": "disabled"}
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        try:
            validate_current(engine)
            checks["database"] = "ok"
        except Exception:
            # A dependency probe reports failure, never fakes a healthy state.
            checks["database"] = "unavailable"
    if settings.r2_enabled:
        client = None
        try:
            client = boto3.client("s3", endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id, aws_secret_access_key=settings.r2_secret_access_key,
                region_name="auto", config=Config(connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1}))
            client.head_bucket(Bucket=settings.r2_bucket)
            checks["storage"] = "ok"
        except Exception:
            checks["storage"] = "unavailable"
        finally:
            if client is not None:
                client.close()
    ready = checks["database"] == "ok" and checks["storage"] in {"ok", "disabled"}
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "not_ready", "checks": checks})
