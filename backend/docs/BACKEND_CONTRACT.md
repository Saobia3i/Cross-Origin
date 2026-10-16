# Backend Contract

Endpoint list: `backend/app/api_catalog.py`, served at `GET /api/catalog`.

## Response envelope (every response)

```json
{
  "ok": true,
  "data": {},
  "meta": { "request_id": "req_xxx", "timestamp": "ISO8601", "source": "dhakapath-backend" },
  "errors": []
}
```

Error: `ok: false`, `data: null`, `errors: [{ "code", "message", "field"?, "details"? }]`.
Unimplemented endpoints return HTTP 501 with code `NOT_IMPLEMENTED`.

## Auth roles
- `guest` – unauthenticated; read-only public endpoints.
- `user` – registered/verified; can report, vote, simulate, ride-share.
- `admin` – moderation, route/hub/fare management.
- Debug endpoints require the `DEMO_SECRET` env value.

## Statuses
- Report: `draft`, `pending`, `verified`, `rejected`, `resolved`, `expired`
- Route edge: `active`, `closed`
- Hub (derived, never stored by hand): `normal`, `delayed`, `blocked`

## Rules
- Simulation is **per session**, not global; it never mutates shared state.
- AI chat must return `reply_text` (not `reply.text`).
- AI output must be grounded in backend data (routes, fares, reports). Unsupported claims are rejected by the grounding check.
- Fare totals for rickshaw/CNG must be **ranges** (`low`/`high`), never a single number.
- Student fare: apply the student fare columns/rules from the fare + rules datasets only where the dataset says a student rule exists.
- Ride-share per-person fare must also be a **range** (total range divided among participants).
