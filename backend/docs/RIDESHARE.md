# Ride-share board & fare-split bot

Service: `app/services/rideshare_service.py` · Endpoints: `app/api_rideshare.py` · Tests: `app/tests/test_rideshare_service.py`.
No payment, no booking: a post is a notice, the bot shows an equal split of an **estimated** total.

## Endpoints (unified envelope; events are global)
| Who | Method & path | Notes |
|---|---|---|
| guest | `GET /api/rideshare/posts` | filters `hub_id` (origin, destination or hub), `origin_hub`, `destination_hub`, `mode`, `status`, `active_only` (default true). Returns `{posts, count}`. |
| verified | `POST /api/rideshare/posts` | body: `mode`, `origin_hub` or `hub_id`, optional `destination_hub`, `seats_total` (2–8), `scenario` (default normal), `notes`, `expires_in_minutes` (5–1440, default 120) |
| verified | `POST /api/rideshare/posts/{id}/join` · `/leave` · `/cancel` | cancel: creator or admin |

Hub names/aliases are resolved like trip search (`UNKNOWN_HUB` 404 / `AMBIGUOUS_HUB` 422). Guests get 401 `UNAUTHORIZED`, unverified users 403 `NOT_VERIFIED`.

## Rules
- Creator occupies seat 1 (`seats_taken` always equals the participant rows). `walk` and `mrt` are rejected (`RIDESHARE_MODE_NOT_SHAREABLE`); every other mode is allowed.
- Join: already a participant → no-op, no event. Full → 409 `RIDESHARE_FULL`; cancelled/expired → 409 `RIDESHARE_NOT_OPEN`. Reaching `seats_total` sets `full`.
- Leave: non-participant → 409 `NOT_A_PARTICIPANT`. A full post reopens (`active`); the last person leaving cancels it (`rideshare.left` then `rideshare.cancelled`). The creator may leave; they can still cancel.
- Cancel: creator or admin, only while open.
- Expiry is lazy (checked on list/get/join/leave/cancel); no event is published for it.
- `active_only=true` means status `active` or `full`; an explicit `status` filter overrides it.

## Fare estimate (never invented)
Taken once at creation from `fare_service.calculate_leg_fare`: an existing route edge for that mode/hub pair if any, else a **transient, unsaved** edge (so an explicit fare row or the formula can price it; no route edge is ever created, warning `NO_ROUTE_EDGE`). If it cannot be priced (no destination, unknown distance, formula disabled…) the post is still created with null fares and warning `FARE_ESTIMATE_UNAVAILABLE`.
The split uses `fare_service.calculate_rideshare_split` over the **current** participants, so per-person amounts fall as people join and rise as they leave:

```json
{"participants": 3, "total_fare_min_bdt": 100, "total_fare_max_bdt": 160,
 "per_person_min_bdt": 33.33, "per_person_max_bdt": 53.33,
 "suggested_safe_per_person_bdt": 54, "currency": "BDT",
 "note": "Estimate split equally. Suggested safe amount is upper-bound ceil."}
```
`suggested_safe_per_person_bdt = ceil(per_person_max)` is an upper-bound suggestion (`fare_disclaimer`), not an official tariff.

## Events
`rideshare.created|joined|left|cancelled`, payload `{post_id, mode, origin_hub, destination_hub, hub_id, seats_total, seats_taken, status, user_id}`; poll with `GET /api/events`.

## Not done
Chat integration (the split is service-level: `recalculate_fare_split(post)`), payments, per-post chat, ride completion/ratings.
