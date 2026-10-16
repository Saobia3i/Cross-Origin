# Routing Engine

Code: `app/services/routing_service.py`. Config: `dataset_config/routing_config.json`. Tests:
`app/tests/test_routing_service.py` (synthetic graph only). No HTTP endpoint yet. Every function that reads data
takes the SQLModel `session` as its first argument, so `search_routes(session, origin_text, destination_text, ...)`.

## Graph
- Nodes are `hubs`, edges are `route_edges`. Built per search by `build_graph(session, session_id, avoid_hubs,
  avoid_modes, scenario=...)`. The DB is never written.
- **Direction:** `bidirectional` → both ways; `one_way` → declared way only; `unknown` → both ways only if the
  edge's `source_dataset` is in `symmetric_sources` (config; today `dhaka_transport_fares`, which lists each
  unordered hub pair once), otherwise origin→destination only, and the leg carries `DIRECTION_UNKNOWN`.
  Co-ordination segments have no direction data, so they are forward-only.
- **Travel time** (scenario time column: `rain`→`time_rain_min`, `peak`→`time_peak_min`, else `time_normal_min`).
  Missing → the normal time (leg warning `TIME_NOT_SCENARIO_SPECIFIC`) → distance ÷ the dataset's mode average speed
  (`TIME_DERIVED`) → `null` (`TIME_UNKNOWN`). Edges with null time are skipped by `fastest` and `balanced`.
- **Transfer wait** is 0 (no dataset rule). Only a simulation's `effects.transfer_wait_min` sets one.

## Disruptions (read-only overlays)
| Source | Effect | Scope |
|---|---|---|
| `route_edges.status='closed'` (admin block) | edge removed | global, persistent |
| **Verified**, unexpired report with `edge_closed` | its `affected_route_ids` removed | global |
| **Verified**, unexpired report with `extra_time_min` | adds minutes (largest wins; duplicates do not stack) | global |
| draft / pending / rejected / resolved / expired report | nothing | – |
| the session's **active** simulation | `effects_json` below | that `session_id` only |

Simulation `effects_json` (empty → defaults for the `scenario_type` from the config):
`fare_scenario`, `time_scenario`, `time_multiplier`, `added_time_min`, `close_flood_prone`, `close_construction_prone`,
`close_zone_edges`, `transfer_wait_min`. With a `zone_hub`, multiplier / added time / closures apply only to edges
touching it; `fare_scenario`/`time_scenario` stay global. A requested `scenario` other than `normal` overrides the
simulation's fare scenario (warning `SCENARIO_CONFLICT`). Flood/construction closures need dataset flags; none exist
yet, so they warn `SIMULATION_NO_EFFECT`.

## Objectives and search
State = (hub, last vehicle mode). Edge costs:
- **cheapest**: `fare_min` (student min if `prefer_student_fare`). Edges without a fare are excluded.
- **fastest**: `time + alert delay + simulation time (+ transfer wait)`. Null-time edges excluded.
- **fewest_transfers**: `1000 × mode change + time`. Tiebreaks only: +0.01 per leg, +1 for an unknown time (never reported).
- **balanced**: `0.4·fare/maxfare + 0.4·time/maxtime + 0.2·transfer + 0.1·risk points`; needs time and fare on
  every edge. If it finds nothing it falls back to cheapest (`BALANCED_FELL_BACK_TO_CHEAPEST`).
- `a_star` uses a Haversine heuristic only for `fastest`, and only when every hub in the graph has coordinates.
  The speed bound is the highest straight-line speed any edge actually achieves, so it never overestimates.
  Otherwise it is Dijkstra with heuristic 0 (`PathResult.algorithm` reports which ran). Today the production data has
  hubs without coordinates, so production searches run Dijkstra.
- **Unique routes:** one path per objective, deduped by ordered `route_id` sequence, then up to 12 penalized re-searches
  (used edges cost more) until there are 3. Fewer → `LESS_THAN_THREE_ROUTES_AVAILABLE`. At most 5 are returned.
  The requested `optimize` result comes first; others follow in objective order. A route that wins several objectives
  lists the extras in `also_best_for`.

## Constraints
`avoid_hubs` (names/aliases/ids; origin and destination are never removed), `avoid_modes` (unknown values are
ignored with a warning), `budget_bdt` (compared with the student minimum when `prefer_student_fare`, else the regular
minimum), `strict_budget` (discards over-budget routes). No route → `routes: []` plus `no_route_reason`
(`NO_ROUTE_WITHIN_CONSTRAINTS` with suggestions, or `NO_ROUTE_FOUND`). Hub resolution failures return
`error.code` `UNKNOWN_HUB` / `AMBIGUOUS_HUB` (with candidates), plus `SAME_ORIGIN_DESTINATION`, `INVALID_OPTIMIZE`,
`SCENARIO_UNKNOWN`.

## Fares and durations
- Leg fares come from `fare_service.calculate_leg_fare`; totals from `calculate_trip_fare`. Regular and student
  ranges are always returned (equal when no discount applies).
- **Metro:** consecutive metro legs are one ride, priced once with the official matrix between its first and last
  station (the first leg carries the fare, the rest show 0 with a `METRO_RIDE` note).
- **Unpriceable legs** (for example metro segments between stops that are not in the matrix) give `null` leg and
  total fares, `FARE_UNKNOWN`, `grounding.checked_fares=false`. Such routes are dropped when 3 fully priced
  routes exist (`ROUTES_OMITTED_FARE_UNKNOWN`) and otherwise listed last. A budget cannot be verified for them
  (`within_budget=false`).
- **Duration:** `total_duration_min = Σ leg travel time + Σ transfer wait`. Walk legs are already inside
  `travel_time_min` and also shown in `walking_time_min`; they are **not added a second time** (adding
  `Σ walking` as well would double-count). No walk edges exist in the data yet.
- Transfers = mode changes between consecutive non-walk legs.

## Hub resolution
`hub_id` → canonical name → alias → fuzzy (rapidfuzz if installed, else containment/difflib ≥ 0.85). An exact alias
always beats fuzzy (`Mirpur` → `MIRPUR_10`). Several fuzzy candidates → `AMBIGUOUS_HUB`; none → `UNKNOWN_HUB` with
suggestions.

## Against the seeded database
Searches take about 0.5 s. Example: Mirpur → Motijheel returns the metro edge (70 BDT, 32 min), the direct bus (32 BDT,
student 16, 63 min) and a metro chain through unlisted stops that has no fare.
