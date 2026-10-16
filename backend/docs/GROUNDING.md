# Grounding

Code: `app/services/grounding_service.py`, `app/api_grounding.py`. An AI-generated (or assistant-facing) payload is
**grounded** only if everything it states matches the database and the engines; anything unverifiable is a violation.
A failing payload is discarded and the deterministic engine result is used (`fallback_action`).

| Check | Rule | Violation codes |
|---|---|---|
| Hubs | every hub id / origin / destination is a real hub id, canonical name or alias (never fuzzy) | `UNKNOWN_HUB` |
| Routes | route ids exist; mode is canonical and matches the route; stated hubs match the route's ends; the route is open and traversable that way; a named line matches; no direct route between unlinked hubs | `UNKNOWN_ROUTE_ID`, `UNKNOWN_MODE`, `ROUTE_NOT_AVAILABLE`, `HALLUCINATED_DIRECT_ROUTE` |
| Connectivity | `leg[n].to_hub == leg[n+1].from_hub`; first/last leg match origin/destination (walk legs are ordinary legs) | `DISCONNECTED_LEGS` |
| Fares | each claimed leg/total range lies inside the engine's range (+/-0.5 BDT rounding); metro equals the official matrix (consecutive metro legs = one ride); no single exact number for a range fare; walk = 0; a scalar `claimed_fare_bdt` must lie in the range; an unpriced leg cannot carry a fare | `FARE_MISMATCH` |
| Student | claimed student fare = engine student fare (local bus 50%, min 10; every other mode equals the regular fare) | `INVALID_STUDENT_RULE` |
| Duration | within max(2 min, 5%) of the engine's time (per leg and total, incl. transfer wait); if the dataset has no time, any stated time is a violation | `DURATION_MISMATCH` |
| Alerts | ids exist; a claimed status matches; "affects routing" only for verified, unexpired, confident alerts | `UNKNOWN_ALERT`, `ALERT_NOT_ACTIVE` |

`NO_PAYLOAD` is returned for an empty payload. Claims are checked in the payload's `scenario` (default normal) and
`session_id`, so a session's simulation changes what counts as correct.

Without full legs (e.g. only `route_ids` + `claimed_*`), trip-level fare/duration claims are compared with the options the
route engine finds between the payload's endpoints; with no real route to compare to they are violations.

**Chat payloads** (`validate_chat_payload`): every option in `payload.route_options`/`routes`, `alerts`, `entities` hubs, and
the fares ("Tk 120", "100 BDT") and minutes ("30 minutes") quoted in `reply_text` must be backed by a grounded option.
This text scan is a heuristic; a quoted number with no grounded route behind it is rejected.

## Endpoints
- `POST /api/grounding/check` `{route_option | chat_response, scenario?, session_id?}` -> `GroundingResult` + `badge`.
- `POST /api/grounding/fake-test` (judge demo) -> `{grounded, blocked, safe_for_display, violations, fallback_action, engine_result}`.
- Both need `DEMO_SECRET` (header `X-Demo-Secret` or body `secret`), as the catalog states (`debug_secret`).
- `GET /api/reports/{id}/reliability` (public) -> votes, confidence/freshness, expiry, admin approval, and plain-language `reasons`.

## Integration
`POST /api/trips/search` grounds every option it returns and adds `data.grounding` (`badge`, `grounded`, `violations`,
`options_checked`, `options_dropped`, `fallback_used`). An option that fails is removed (`GROUNDING_FAILED` warning), so
nothing ungrounded is shown. `/api/fares/estimate` is computed only from database routes, so it is not wrapped. The chat feature
should call `validate_chat_payload` and replace a failing reply with the engine output.
