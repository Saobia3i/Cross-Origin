"""Register / login / profile. Registration never grants verification or admin rights."""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.deps import get_current_user, get_db
from app.enums import UserStatus
from app.models import User, utcnow
from app.responses import api_error, success
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserPublic
from app.security import create_access_token, hash_password, verify_password

router = APIRouter()


def _token_payload(user: User) -> dict:
    token = create_access_token(str(user.id), {"role": user.role})
    return TokenResponse(access_token=token, user=UserPublic.model_validate(user)).model_dump(mode="json")


@router.post("/api/auth/register")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.exec(select(User).where(User.email == email)).first() is not None:
        raise api_error("EMAIL_TAKEN", "An account with this email already exists.", "email")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                role="user", is_verified=False, created_at=utcnow(), updated_at=utcnow())
    db.add(user)
    db.commit()
    db.refresh(user)
    return success(_token_payload(user), status_code=201)


@router.post("/api/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.exec(select(User).where(User.email == body.email.strip().lower())).first()
    # same message for unknown email and wrong password
    if user is None or not verify_password(body.password, user.password_hash):
        raise api_error("INVALID_CREDENTIALS", "Wrong email or password.")
    if user.status != UserStatus.active.value:
        raise api_error("ACCOUNT_DISABLED", "This account is disabled.")
    return success(_token_payload(user))


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return success(UserPublic.model_validate(user).model_dump(mode="json"))
