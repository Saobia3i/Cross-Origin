# Transit & Trip APIs

All responses use the standard envelope. Code: `app/api.py`; read models in `app/services/transit_service.py`;
search cache in `app/services/search_cache.py`. Schemas: `app/schemas.py`.
All six endpoints are public. Every GET accepts an optional `session_id` so statuses reflect that session's
simulation (other sessions are unaffected).

## GET endpoints

| Endpoint | Data |
|---|---|
| `/api/transit/network` | `{hubs, routes, fare_rules, mode_rates, scenario_multipliers}`. `?include_geometry=true` adds route geometry (large). |
| `/api/transit/hubs` | list of hubs with `status`, `status_reason`, `active_alert_count` |
| `/api/transit/routes` | list of routes; filters `mode`, `hub_id`, `status`, `include_geometry` |
| `/api/transit/hub-statuses` | `{hub_statuses: [{hub_id, status, reason, active_alert_ids}]}` |

**Derived hub status (never stored).** From the same overlay the router uses (admin closures + verified, unexpired
alerts + this session's simulation):
- `blocked`: every route touching the hub is closed (admin, verified alert, or this session's simulation).
- `delayed`: not blocked, and one of: some (not all) routes closed; a verified alert adds delay at the hub or on a
  route touching it; this session's simulation targets the hub or slows a route there.
- `normal`: otherwise.
`active_alert_count` / `active_alert_ids` count **verified, unexpired** reports touching the hub (its
`affected_hub`, or one of its routes). Draft, pending, rejected, resolved and expired reports never count. There is
no hub-override table yet, so no admin override is applied. A partial *admin* closure also shows as `delayed`.

**Routes.** `status` is the effective status for the session (`closed` if admin-closed, closed by a verified alert, or
closed by the session's simulation). `closed_reason` says why and `closed_by` is `admin`, `verified_alert` or
`simulation`. `extra_time_min` is the reported delay (alerts plus simulation). `active_alert_ids` are verified alerts on it.

## POST /api/trips/search

Structured body (all optional except `origin`+`destination`, or `query_text`):
`origin, destination, budget_bdt, strict_budget, prefer_student_fare, avoid_hubs, avoid_modes, optimize
(cheapest|fastest|fewest_transfers|balanced), session_id, scenario, query_text`.

`data`: `query_echo, routes[RouteOption], no_route_reason, session_id, active_scenario, origin, destination,
optimize, scenario, disruptions, warnings, warning_codes`. Route options are exactly those from
`routing_service.search_routes` (see `ROUTING_ENGINE.md`); every fare is a min/max range and every leg has
`student_discount_applies`. `session_id` is `null` if the request had none; no id is invented.

**`query_text`** is used only when `origin` and `destination` are not both given (structured fields win). It calls
`app.services.nlp_service.parse_query(query_text, session_id=None) -> dict` (keys: the structured fields above;
or `{"error": {...}}`). Explicit structured fields override parsed ones. Without that module the endpoint returns
HTTP 501 `NLP_NOT_IMPLEMENTED` with `details.suggested_fields`. Routes are never guessed from raw text.

**Errors** (envelope `ok:false`):

| Code | HTTP | When |
|---|---|---|
| `UNKNOWN_HUB` | 404 | no hub matches origin/destination (`field`, `details.suggestions`) |
| `AMBIGUOUS_HUB` | 422 | several hubs match (`details.candidates`) |
| `SAME_ORIGIN_DESTINATION`, `INVALID_OPTIMIZE` | 422 | |
| `INVALID_SCENARIO` | 422 | unknown scenario |
| `NLP_NOT_IMPLEMENTED` | 501 | `query_text` without an NLP service |
| `NLP_PARSE_FAILED`, `NLP_INCOMPLETE_QUERY` | 422 | the parser failed / found no origin+destination |
| `VALIDATION_ERROR` | 422 | bad request shape (negative budget, no origin/destination or text…) |

**No route is not an error.** The response is HTTP 200, `ok:true`, `routes: []` and
`no_route_reason: {code: NO_ROUTE_FOUND | NO_ROUTE_WITHIN_CONSTRAINTS, message, suggestions[]}` (also added to
`warnings`), so the UI can show the suggestions. The two codes are registered in `ERROR_STATUS`
(`app/responses.py`) with HTTP 200 to make this explicit.

## POST /api/fares/estimate

Body: `route_option_id?`, `legs?[{route_id, mode?}]`, `prefer_student_fare`, `scenario`, `session_id?`.
- A `route_option_id` found in the **recent-search cache** (keyed by `session_id` + option id, replaced by that
  session's next search, 30 min TTL, process-local) uses its cached legs (`legs_source: cached_route_option`;
  any `legs` sent are ignored with `LEGS_IGNORED`).
- Otherwise `legs` are priced directly (`request_legs`). Each `route_id` must exist (`ROUTE_NOT_FOUND`) and `mode`,
  if given, must match (`LEG_MODE_MISMATCH`). Legs are oriented along the shared hub; consecutive metro legs are
  priced once as a ride. Neither given/found → `ROUTE_OPTION_NOT_FOUND` (404).
- A leg with no dataset fare → `FARE_CALCULATION_FAILED` (422) with `details.route_id` and the underlying code.
- `data` = `TripFareResult` (`total_regular_*`, `total_student_*`, `legs[]` with `student_discount_applies`,
  `student_discount_applies_any_leg`, `warnings`) plus `route_option_id, scenario, prefer_student_fare,
  session_id, legs_source`. With `prefer_student_fare=false` the student fields mirror the regular fare. The
  session simulation's fare scenario applies unless a non-normal `scenario` is requested (`SCENARIO_CONFLICT`).
