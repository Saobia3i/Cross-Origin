from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import router
from app.config import settings
from app.responses import ApiError, error, error_item
from app.services import alert_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.db import create_db_and_tables

        create_db_and_tables()
    except Exception as exc:  # DB problems must not stop the app from booting
        print(f"[startup] DB init skipped: {exc}")
    yield


app = FastAPI(title="DhakaPath Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return error(exc.code, exc.message, exc.status_code, exc.field, exc.details)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}.get(
        exc.status_code, "HTTP_ERROR")
    return error(code, str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errs = [
        error_item("VALIDATION_ERROR", e["msg"], ".".join(str(p) for p in e["loc"][1:]) or None)
        for e in exc.errors()
    ]
    return error("VALIDATION_ERROR", "Invalid request.", 422, errors=errs)


app.include_router(router)
# Public report photos: backend/data/uploads/reports/{report_id}/<file> -> /uploads/reports/{report_id}/<file>
app.mount("/uploads/reports", StaticFiles(directory=str(alert_service.upload_root() / "reports"), check_dir=False),
          name="report-uploads")
