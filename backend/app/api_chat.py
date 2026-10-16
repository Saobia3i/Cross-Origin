"""Chat endpoints. The frontend sends the prompt; the backend calls the engines and returns a structured reply."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.deps import get_db, get_optional_user
from app.models import ChatMessage, ChatSession, User
from app.responses import api_error, success
from app.schemas import ChatMessageOut, ChatRequest
from app.services import chat_service

router = APIRouter()


@router.post("/api/chat")
def chat(body: ChatRequest, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """One chat turn. Guests may chat and prepare report drafts; submitting needs a verified user."""
    session_id = body.session_id or f"chat_{uuid.uuid4().hex[:12]}"
    response = chat_service.handle_chat(db, session_id, body.message, user, body.ui_language)
    return success(response.model_dump(mode="json"))


@router.get("/api/chat/sessions/{session_id}")
def chat_session(session_id: str, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Recent messages (oldest first) and the server-side context_state of a chat session."""
    row = db.get(ChatSession, session_id)
    if row is None:
        raise api_error("CHAT_SESSION_NOT_FOUND", f"No chat session '{session_id}'.", details={"session_id": session_id})
    recent = db.exec(select(ChatMessage).where(ChatMessage.session_id == session_id)
                     .order_by(ChatMessage.id.desc()).limit(limit)).all()
    return success({"session_id": session_id, "created_at": row.created_at,
                    "messages": [ChatMessageOut.model_validate(m).model_dump(mode="json") for m in reversed(recent)],
                    "context_state": chat_service.load_context(db, session_id)})
