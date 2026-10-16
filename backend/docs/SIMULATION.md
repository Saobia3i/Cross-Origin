# Session Simulation

Code: `app/services/simulation_service.py`, `app/api_simulations.py`; integrated in `routing_service.build_graph`.
A simulation belongs to one `session_id` (one active per session; a new one replaces it; default lifetime 120 min,
`routing_config.json -> simulation_ttl_min`). It is **virtual**: effects are recomputed from the dataset every time a
graph is built and `route_edges` is never written. Admin block/unblock and verified alerts remain the only global effects.

| scenario_type | fare scenario | time | closures |
|---|---|---|---|
| heavy_rain | rain | `time_rain_min` | `flood_prone` edges (in `zone_hub` if given, else all) |
| peak_rush_hour | peak | `time_peak_min` | none |
| blocked_intersection | detour | `time_detour_min` | active non-walk edges touching `zone_hub` (required) |
| regular_holiday / festive_holiday | same name | none | none |

Fares use the explicit scenario columns, else dataset multipliers (fare_service). Where a scenario time is missing the
normal time is used and `TIME_RAIN_DATA_MISSING` / `TIME_PEAK_DATA_MISSING` / `TIME_DETOUR_DATA_MISSING` is reported
(no multiplier is invented; metro stays null). `NO_FLOOD_PRONE_DATA` appears when the dataset flags no flood-prone edge
(true today, so heavy_rain closes nothing). Extra transfer wait comes only from `scenario_transfer_wait_min` (empty = 0).
An explicit `effects_json` on a simulation row still overrides the derived effects.

## Endpoints (verified user)
- `POST /api/simulations` `{session_id, scenario_type, zone_hub?, origin?, destination?, prefer_student_fare?}` -> 201
  `{scenario_id, session_id, scenario_type, status, effects, broken_segments, recalculated_routes, before_after_comparison}`.
  The compared journey is origin/destination if given, else the session's last `/api/trips/search`; otherwise
  `before_after_comparison.note = NO_JOURNEY`. Before = no simulation, after = this session's simulation (verified alerts in both).
  `broken_segments` = routes of the original best route that the simulation closes.
- `POST /api/simulations/reset` `{session_id}` -> `status: reset` (or `none` if nothing was active).
- `GET /api/simulations/active?session_id=` -> `{active, scenario_id, scenario_type, zone_hub, effects, ...}`.

Comparison fields: `original_/new_` `route_id(s)`, `duration_min`, `fare_min/max`, plus `added_time_min`, `added_fare_min/max`
(null when a side is unknown, e.g. metro segments without times), `rerouted`, and the full `before` / `after` options.
Events (session-scoped, see `GET /api/events?session_id=`): `simulation.applied` + `route.recalculated`, `simulation.reset` + `route.recalculated`.
