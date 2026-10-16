"""Grounding endpoints (judge demo) and report reliability. See docs/GROUNDING.md."""
from typing import Optional

from fastapi import APIRouter, Depends, Header
from sqlmodel import Session

from app.deps import check_demo_secret, get_db
from app.responses import success
from app.schemas import FakeGroundingRequest, GroundingCheckRequest
from app.services import grounding_service as gs

router = APIRouter()


@router.post("/api/grounding/check")
def grounding_check(body: GroundingCheckRequest, x_demo_secret: Optional[str] = Header(None, alias="X-Demo-Secret"),
                    db: Session = Depends(get_db)):
    """Validate an AI route option or chat response against the database and engines (DEMO_SECRET required)."""
    check_demo_secret(x_demo_secret or body.secret)
    if body.route_option is not None:
        result = gs.validate_ai_route_option(db, body.route_option, body.scenario, body.session_id)
    else:
        result = gs.validate_chat_payload(db, body.chat_response, body.scenario, body.session_id)
    return success(result.model_dump(mode="json") | {"badge": result.badge()})


@router.post("/api/grounding/fake-test")
def grounding_fake_test(body: FakeGroundingRequest, x_demo_secret: Optional[str] = Header(None, alias="X-Demo-Secret"),
                        db: Session = Depends(get_db)):
    """Judge demo: a hallucinated route is checked and shown blocked, next to the real engine answer."""
    check_demo_secret(x_demo_secret or body.secret)
    payload = body.model_dump(exclude={"secret", "session_id", "scenario"}, exclude_none=True)
    return success(gs.fake_grounding_test(db, payload, body.scenario, body.session_id))


@router.get("/api/reports/{report_id}/reliability")
def report_reliability(report_id: str, db: Session = Depends(get_db)):
    return success(gs.build_reliability_payload(db, report_id))
