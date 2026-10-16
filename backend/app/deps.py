"""Request dependencies: database session and bearer-token authentication.

Tokens are minted by app.security.create_access_token (sub = user id). The user row is re-read on every
request, so role / verification / disabled status always come from the database, not from the token.
"""
import hmac

import jwt
from fastapi import Depends, Request
from sqlmodel import Session

from app.db import get_session
from app.enums import UserRole, UserStatus
from app.models import User
from app.responses import api_error
from app.security import decode_access_token


def get_db():
    yield from get_session()


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The authenticated user, or None for a guest. A token that is present but bad is a 401, not a guest."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if not header:
        return None
    if scheme.lower() != "bearer" or not token.strip():
        raise api_error("INVALID_TOKEN", "Authorization header must be 'Bearer <token>'.")
    try:
        claims = decode_access_token(token.strip())
    except jwt.ExpiredSignatureError:
        raise api_error("TOKEN_EXPIRED", "Your session has expired. Please log in again.")
    except jwt.PyJWTError:
        raise api_error("INVALID_TOKEN", "The access token is not valid.")
    try:
        user = db.get(User, int(claims.get("sub", "")))
    except (TypeError, ValueError):
        user = None
    if user is None:
        raise api_error("INVALID_TOKEN", "The access token does not belong to a known user.")
    if user.status != UserStatus.active.value:
        raise api_error("ACCOUNT_DISABLED", "This account is disabled.")
    return user


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise api_error("UNAUTHORIZED", "Log in to do this. Guests can only browse.")
    return user


def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_verified:
        raise api_error("NOT_VERIFIED", "Only verified users can do this.")
    return user


def get_current_admin(user: User = Depends(get_current_verified_user)) -> User:
    if user.role != UserRole.admin.value:
        raise api_error("FORBIDDEN", "Admin access required.")
    return user


def check_demo_secret(supplied: str | None) -> None:
    """Guard for demo/debug endpoints: DEMO_SECRET via header X-Demo-Secret (or a body secret field)."""
    from app.config import settings

    if not hmac.compare_digest((supplied or "").encode(), settings.demo_secret.encode()):
        raise api_error("DEBUG_SECRET_INVALID", "Missing or wrong demo secret.")
