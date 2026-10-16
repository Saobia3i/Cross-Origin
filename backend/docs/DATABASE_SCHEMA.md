# Database Schema & Seeding

SQLite at `backend/data/dhakapath.db` (`DATABASE_URL`). Tables: `app/models.py`. Allowed values for every
enum-like column: `app/enums.py`; the DB enforces them with CHECK constraints. SQLite foreign keys are ON.

| Group | Tables |
|---|---|
| Accounts | `users` |
| Dataset-derived network | `hubs`, `route_edges`, `fare_matrix_rows`, `metro_fare_matrix`, `mode_rates`, `scenario_multipliers`, `wealth_indices` |
| Admin rules | `fare_rules` (one editable row per transit mode) |
| Reports | `reports`, `report_images`, `report_votes` (unique report+user, value ±1), `report_drafts` |
| Simulation & events | `simulations` (one active per session: enforced in service logic), `events` |
| Chat | `chat_sessions`, `chat_messages`, `chat_context`, `llm_cache` |
| Ride-share | `rideshare_posts`, `rideshare_joins` (unique post+user) |

## Seeding

```bash
python -m app.scripts.inspect_datasets && python -m app.scripts.normalize_datasets   # produce normalized_*.json
python -m app.scripts.seed --source normalized          # production seed
python -m app.scripts.seed --source fixtures --reset    # synthetic test fixtures (test-only)
python -m app.scripts.reset_db                          # empty tables
```

- `--reset` deletes the SQLite file (or drops all tables if the file is locked) before seeding.
- Switching between `normalized` and `fixtures` requires `--reset`; the seed refuses to mix them.
- Missing `normalized_*.json` files are not an error: demo users and default `fare_rules` are seeded, the
  network stays empty, and a warning is printed. Nothing is invented.
- Seeding is idempotent. Re-running changes nothing. Dataset tables are refreshed from the source
  and stale rows are removed. Runtime state is never overwritten:
  - `route_edges.status` / `closed_reason`
  - `fare_rules`
  - `reports`
  - `users`
- Reports are seeded only from `tests/fixtures/mini_datasets/reports_TEST_FIXTURE.json` (fixtures mode) or
  an optional `data/generated/normalized_reports.json`. Production starts with no reports.
- Demo users: `admin@dhakapath.local` (admin), `user1..3@dhakapath.local` (user), all verified,
  password `password123`. Change them before any real deployment.

## Mapping decisions (dataset → schema)

- **Modes:** normalized `mode_code` → `TransitMode`.
  `LOCAL_BUS`→`local_bus`, `MRT6`→`mrt`, `RICKSHAW`→`rickshaw`, `CNG`→`cng`, `CAR`→`car`,
  `MOTORBIKE`→`motorbike`, `PATHAO_BIKE`/`PATHAO_CAR`/`UBER_CAR`/`UBER_MOTO`→ same name lowercased.
  `leguna` and `walk` have no dataset rows; they exist only in `fare_rules`.
- **Hub type:** `metro_station` if the hub is a metro station per the data (a stop on a metro route or a
  matrix station), else `hub`. `interchange` and `landmark` are never assigned automatically because
  the data does not say.
- **Direction:** `one_way` / `bidirectional` only when the dataset states it; otherwise `unknown`.
  All current edges are `unknown`.
- **`scenario_multipliers.scenario`** uses the dataset's names: `normal, rain, peak, peak_plus_rain,
  detour, regular_holiday, festive_holiday`. `multiplier` is NULL where the dataset states none (all
  `peak_plus_rain`). `applies_to` is `fare` (the dataset says "Applied to normal fare").
  Simulation scenarios map onto them like this:

  | Simulation `scenario_type` | Multiplier `scenario` |
  |---|---|
  | `heavy_rain` | `rain` |
  | `peak_rush_hour` | `peak` |
  | `detour` | `detour` |
  | `blocked_intersection` | no multiplier (affects routes) |
  | `regular_holiday` | `regular_holiday` |
  | `festive_holiday` | `festive_holiday` |

- **`fare_rules` defaults:** student discount and minimum come from the student rule ("50% off bus only,
  min Tk 10") so `local_bus` = 50% / Tk 10 and every other mode = 0.
  `surge_allowed` is true only for modes whose fare rows say surge applies (the app-based ones).
  `use_official_matrix` is true only for `mrt`.
  `allow_formula_fallback` is false for `mrt` and `walk`.
- **`route_edges.student_eligible`** follows the mode's `fare_rules` student discount (bus only today).
- **Left NULL on purpose** (no source): `straight_line_km`, `time_detour_min`, and the boarding,
  alighting and transfer notes (empty arrays).
- **`hubs.wealth_index` / `area_wealth_note`** come from the hub wealth-index rules. Hubs not listed there
  stay NULL.
- **`wealth_indices`** holds the 15 hub wealth rows. Per-fare-row area multipliers live in
  `fare_matrix_rows`.
