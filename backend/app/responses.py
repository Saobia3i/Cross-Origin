"""Unified response envelope helpers."""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

SOURCE = "dhakapath-backend"


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def make_meta(request_id: str | None = None) -> dict[str, str]:
    return {
        "request_id": request_id or new_request_id(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE,
    }


def success(data: Any = None, status_code: int = 200, request_id: str | None = None) -> JSONResponse:
    body = {"ok": True, "data": {} if data is None else data, "meta": make_meta(request_id), "errors": []}
    return JSONResponse(jsonable_encoder(body), status_code=status_code)


def error_item(code: str, message: str, field: str | None = None,
               details: dict | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "field": field, "details": details or {}}


def error(code: str, message: str, status_code: int = 400, field: str | None = None,
          details: dict | None = None, errors: list[dict] | None = None,
          request_id: str | None = None) -> JSONResponse:
    body = {
        "ok": False,
        "data": None,
        "meta": make_meta(request_id),
        "errors": errors or [error_item(code, message, field, details)],
    }
    return JSONResponse(body, status_code=status_code)


def not_implemented(what: str = "") -> JSONResponse:
    suffix = f": {what}" if what else ""
    return error("NOT_IMPLEMENTED", f"Endpoint not implemented yet{suffix}.", 501)


class ApiError(Exception):
    """Raise anywhere; main.py converts it into the error envelope."""

    def __init__(self, code: str, message: str, status_code: int = 400,
                 field: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.field, self.details = field, details


# HTTP status for every error code the transit / trip / fare endpoints can return.
ERROR_STATUS = {
    "VALIDATION_ERROR": 422,
    "UNKNOWN_HUB": 404,
    "AMBIGUOUS_HUB": 422,
    "SAME_ORIGIN_DESTINATION": 422,
    "INVALID_OPTIMIZE": 422,
    "INVALID_SCENARIO": 422,
    "NLP_NOT_IMPLEMENTED": 501,
    "NLP_PARSE_FAILED": 422,
    "NLP_INCOMPLETE_QUERY": 422,
    "ROUTE_OPTION_NOT_FOUND": 404,
    "ROUTE_NOT_FOUND": 404,
    "LEG_MODE_MISMATCH": 422,
    "FARE_CALCULATION_FAILED": 422,
    # auth
    "UNAUTHORIZED": 401,
    "INVALID_TOKEN": 401,
    "TOKEN_EXPIRED": 401,
    "NOT_VERIFIED": 403,
    "ACCOUNT_DISABLED": 403,
    "FORBIDDEN": 403,
    # reports, votes, drafts, events
    "REPORT_NOT_FOUND": 404,
    "DRAFT_NOT_FOUND": 404,
    "DRAFT_FORBIDDEN": 403,
    "DRAFT_NOT_OPEN": 409,
    "DRAFT_EXPIRED": 409,
    "DRAFT_INVALID": 422,
    "INVALID_EVENT_TYPE": 422,
    "INCIDENT_TIME_FUTURE": 422,
    "INCIDENT_TIME_TOO_OLD": 422,
    "FARE_SURGE_FIELDS_REQUIRED": 422,
    "INVALID_STATUS_TRANSITION": 409,
    "REPORT_NOT_VOTABLE": 409,
    "CANNOT_VOTE_OWN_REPORT": 403,
    "NO_VOTE_TO_RETRACT": 404,
    "EDGE_CLOSED_REQUIRES_ROUTES": 422,
    "IMAGE_TOO_MANY": 422,
    "IMAGE_TYPE_NOT_ALLOWED": 422,
    "IMAGE_TOO_LARGE": 422,
    "IMAGE_INVALID": 422,
    "DEBUG_SECRET_INVALID": 403,
    # Reported inside a successful trip-search response as data.no_route_reason.code (HTTP 200):
    "NO_ROUTE_FOUND": 200,
    "NO_ROUTE_WITHIN_CONSTRAINTS": 200,
}


def api_error(code: str, message: str, field: str | None = None, details: dict | None = None) -> ApiError:
    return ApiError(code, message, ERROR_STATUS.get(code, 400), field, details)
