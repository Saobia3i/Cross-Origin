"""Shared test helpers for the report / alert tests."""
import struct
import zlib

from sqlmodel import Session, select

from app.models import User
from app.security import create_access_token
from app.services.seeder import seed

MRT_U_M = "MRT6_UTTARA_NORTH_MIRPUR_10"
MRT_M_F = "MRT6_MIRPUR_10_FARMGATE"
BUS_F_U = "LOCAL_BUS_FARMGATE_UTTARA_NORTH"
ADMIN, USER1, USER2, USER3 = ("admin@dhakapath.local", "user1@dhakapath.local",
                              "user2@dhakapath.local", "user3@dhakapath.local")


def user_by_email(session: Session, email: str) -> User:
    return session.exec(select(User).where(User.email == email)).one()


def token_for(user: User) -> str:
    return create_access_token(str(user.id), {"role": user.role})


def headers_for(session: Session, email: str) -> dict:
    return {"Authorization": f"Bearer {token_for(user_by_email(session, email))}"}


def make_user(session: Session, email: str, *, verified: bool = True, role: str = "user", status: str = "active") -> User:
    user = User(email=email, name=email.split("@")[0], password_hash="x", role=role, is_verified=verified, status=status)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def report_payload(**overrides) -> dict:
    base = {"report_type": "waterlogging", "severity": "heavy", "affected_hub": "MIRPUR_10",
            "affected_route_ids": [MRT_M_F], "description": "Knee-deep water near the junction"}
    base.update(overrides)
    return base


def png_bytes(size: int = 0) -> bytes:
    """A real (tiny) PNG; `size` pads the file with an ancillary chunk to reach at least that many bytes."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    if size:
        raw += chunk(b"tEXt", b"k\x00" + b"x" * max(0, size - len(raw) - 16))
    return raw + chunk(b"IEND", b"")


def jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def webp_bytes() -> bytes:
    return b"RIFF" + struct.pack("<I", 12) + b"WEBP" + b"VP8 " + b"\x00" * 8
