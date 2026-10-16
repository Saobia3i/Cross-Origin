# Architecture

```
Frontend (React)  ──HTTP/JSON, unified envelope──▶  FastAPI app (api*.py)  ──▶  services/  ──▶  SQLite (SQLModel)
                                                          │                          │
                         polls GET /api/events ◀──────────┤                          └─▶ LLM providers (optional)
                                                          └─ grounding on every AI-facing answer
```
Everything is under `backend/`. Datasets (CSV/JSON) are normalized by scripts into `data/generated/`, then seeded into SQLite;
the API never reads raw files.

## Layers
- **API** (`api.py`, `api_auth|reports|simulations|grounding|chat|rideshare.py`): validation, auth dependencies (`deps.py`: guest /
  verified / admin, bearer JWT re-checked against the DB), envelope + error codes (`responses.py`). `api_catalog.py` is the single list of
  endpoints; anything not implemented answers 501 `NOT_IMPLEMENTED`.
- **Services** (pure functions taking a `session`):
  - `fare_service` – metro matrix, explicit fare rows, formula fallback, student rule, ranges, ride-share split.
  - `routing_service` – graph from hubs/route edges, Dijkstra/A*, ≥3 unique options, objectives, constraints, overlays.
  - `alert_service` / `alert_rules` – drafts → pending → verified, votes, confidence decay, expiry. Only verified, unexpired alerts
    with live confidence ≥ 0.40 affect routing; the admin chooses closures/delays.
  - `simulation_service` – per-session what-ifs (rain, rush hour, blocked intersection, holidays). Virtual overlay keyed by
    `session_id`; never writes `route_edges`; other sessions are unaffected.
  - `rideshare_service` – board with seats, join/leave/cancel, equal fare split (below).
  - `event_service` – append-only log written in the caller's transaction.
  - `nlp_service`, `llm_service`, `chat_service` – chat pipeline.
  - `grounding_service` – the guard (below).
- **SQLite** (`data/dhakapath.db`, 21 tables). Tests and rehearsals use throwaway files.

## The AI boundary
The LLM (OpenRouter → Gemini → Groq, all free tiers, all optional, temperature 0, strict JSON, cached) is used for exactly two things:
1. extract the **intent and entities** from the user's words, and
2. optionally **rewrite** a reply that the backend already composed from engine facts.

It never supplies routes, fares, hubs, alerts or durations. Hub names it returns are resolved against the database; a rewrite is
accepted only if it adds no new number. Without any key, the rule-based parser and templates give the same facts.

## Grounding blocks hallucination
Every route option, trip-search result and chat payload is validated against the database before it leaves the backend
(`UNKNOWN_ROUTE_ID`, `UNKNOWN_HUB`, `FARE_MISMATCH`, `DURATION_MISMATCH`, `DISCONNECTED_LEGS`, `UNKNOWN_ALERT`, …). Ungrounded options
are dropped, ungrounded chat text falls back to the template (`fallback_used`), and an invented route is shown as blocked
(`/api/grounding/fake-test`, shielded by `DEMO_SECRET`). There is no endpoint that returns raw model output as a route.

## Alerts, routing and simulation
Reports are crowd-sourced evidence; `pending` reports change nothing. Votes raise confidence, an admin verifies (`affects_routing`),
confidence decays and the alert expires. Search loads the verified alerts and the caller's active simulation as overlays on the
immutable dataset graph, so results can differ per session while the stored network stays the real data. Reset removes the overlay.

## Event polling
`events` rows are inserted in the same transaction as the change they describe, so an event exists exactly when the change was
committed. `GET /api/events?since_id=&session_id=&limit=` returns global events plus that session's own events, ordered by id, with
`latest_id` as the cursor for the next poll. Types: `report.*`, `route.recalculated`, `simulation.*`, `chat.message`, `rideshare.*`.

## Ride-share bot
A post stores a total fare estimate taken once from `fare_service` (an existing edge, else a transient unsaved edge priced from the
fare data/formula; null + `FARE_ESTIMATE_UNAVAILABLE` if it cannot be priced). On each read the split is recomputed with
`calculate_rideshare_split` over the current participants: per-person min/max range and `suggested_safe_per_person_bdt = ceil(max)`,
labelled an upper-bound suggestion, not a tariff. No payment is handled.

## Known limits
No flood/construction flags in the data (rain simulation closes nothing), some hubs lack coordinates (Dijkstra instead of A*),
admin route/hub/fare-rule editing is not implemented, the search cache is in memory, Bangla-script chat input is detected but not parsed.
