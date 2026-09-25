import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.exceptions import HTTPException

from .config import Settings
from .database import Database
from .db_models import DatabaseState, UserRecord
from .errors import ApiError
from .models import ErrorResponse
from .routers import analysis, auth, projects, transcripts, uploads, usage
from .store import Store

logger = logging.getLogger(__name__)


def create_app(
    database: Database | None = None,
    settings: Settings | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    settings = settings or Settings()
    owns_database = database is None
    database = database or Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            database.initialize()
            with database.transaction() as session:
                marker = session.get(DatabaseState, 1)
                if settings.seed_demo_data and not marker.demoSeeded:
                    if session.scalar(select(UserRecord.id).limit(1)) is None:
                        Store(session, settings, clock).seed()
                    marker.demoSeeded = True
            yield
        finally:
            if owns_database:
                database.dispose()

    app = FastAPI(
        title="AI Shorts Generator",
        lifespan=lifespan,
        version="0.1.0",
        description=(
            "SQLAlchemy-backed demo backend with persistent data. Media processing is simulated. "
            "Sign in with email and password at /v1/auth/google, then use a bearer token. "
            "The historical route name does not imply Google OAuth."
        ),
        responses={
            422: {"model": ErrorResponse, "description": "Invalid request fields."},
            "default": {"model": ErrorResponse, "description": "Stable API error envelope."},
        },
    )
    app.state.database = database
    app.state.settings = settings
    app.state.clock = clock
    app.state.fail_next = set()  # Internal simulator test hook, not persisted user data.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo input: validation errors can contain submitted passwords.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Some request fields are missing or invalid.",
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "NOT_FOUND" if exc.status_code == 404 else "INVALID_INPUT",
                    "message": "Route not found."
                    if exc.status_code == 404
                    else "Request not supported.",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.exception("Unhandled backend error")
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "SERVER_ERROR", "message": "Something went wrong. Try again."}
            },
        )

    for router in (
        auth.router,
        uploads.router,
        projects.router,
        transcripts.router,
        analysis.router,
        usage.router,
    ):
        app.include_router(router)
    return app


app = create_app()
