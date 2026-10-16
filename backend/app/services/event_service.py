"""Append-only event log used for polling (`GET /api/events?since_id=`).

Events are added to the caller's transaction (flushed, not committed), so an event exists exactly when the
change it describes was committed.

Visibility: events with session_id NULL are global (report/route events); events with a session_id
(draft events, later simulation events) are only returned to that session.
"""
from __future__ import annotations

from sqlmodel import Session, or_, select

from app.enums import EventType, values
from app.models import Event
from app.responses import ApiError

MAX_LIMIT = 500


def publish_event(session: Session, event_type: str | EventType, payload: dict | None = None,
                  session_id: str | None = None, report_id: str | None = None,
                  route_id: str | None = None) -> Event:
    value = event_type.value if isinstance(event_type, EventType) else event_type
    if value not in values(EventType):
        raise ApiError("INVALID_EVENT_TYPE", f"Unknown event type {value!r}.", 422, "event_type",
                       {"allowed": values(EventType)})
    event = Event(event_type=value, session_id=session_id, report_id=report_id, route_id=route_id,
                  payload_json=payload or {})
    session.add(event)
    session.flush()  # assigns event_id; the caller commits
    return event


def get_events(session: Session, since_id: int = 0, session_id: str | None = None, limit: int = 100,
               event_type: str | None = None) -> list[Event]:
    """Events with event_id > since_id, oldest first. Global events plus, if given, that session's own."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    visible = Event.session_id.is_(None)
    if session_id:
        visible = or_(visible, Event.session_id == session_id)
    query = select(Event).where(Event.event_id > max(0, int(since_id)), visible)
    if event_type:
        query = query.where(Event.event_type == event_type)
    return list(session.exec(query.order_by(Event.event_id).limit(limit)).all())


def latest_event_id(session: Session) -> int:
    last = session.exec(select(Event.event_id).order_by(Event.event_id.desc()).limit(1)).first()
    return last or 0
