import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.attachments import attachments_router, task_attachments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.comments import comments_router
from app.api.v1.comments import project_tasks_router as comments_project_router
from app.api.v1.projects import router as projects_router
from app.api.v1.tasks import project_tasks_router, tasks_router
from app.api.v1.users import router as users_router
from app.config import settings
from app.core.exceptions import AppException
from app.core.logging import setup_logging

app = FastAPI(
    title=settings.APP_NAME,
    description="Task Management API",
    version=settings.APP_VERSION,
)

setup_logging()

logger = structlog.get_logger()

app.mount(
    f"/{settings.UPLOAD_DIR}",
    StaticFiles(directory=settings.UPLOAD_DIR, check_dir=False),
    name="uploads",
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000)
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.exception_handler(AppException)
async def app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "fields": [
                    {
                        "field": ".".join(
                            str(loc) for loc in err["loc"][1:]
                        ),  # skip "body"
                        "message": err["msg"],
                    }
                    for err in exc.errors()
                ],
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    _request: Request, exc: Exception
) -> JSONResponse:
    logger.error("unhandled_exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            }
        },
    )


app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(project_tasks_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(comments_project_router, prefix="/api/v1/projects")
app.include_router(comments_router, prefix="/api/v1/comments")
app.include_router(task_attachments_router, prefix="/api/v1")
app.include_router(attachments_router, prefix="/api/v1")


@app.get("/")
def root():
    return {"message": "Hello World!"}


@app.get("/health")
def health():
    return {"status": "ok"}
