# Fare Engine

Code: `app/services/fare_service.py`. Tests: `app/tests/test_fare_service.py`. Every function that reads data
takes the SQLModel `session` as its first argument. Fares are **ranges** (`min_bdt`/`max_bdt`); money is
kept at full precision and rounded to 2 decimals only when serialized. No endpoint uses it yet.

## Precedence per leg (`calculate_leg_fare`)

1. **Walk**: 0 / 0.
2. **Metro** (`mrt`): `metro_fare_matrix`, exact (min = max, `is_range=false`). No scenario, wealth or student
   adjustments, and metro rows in `fare_matrix_rows` are ignored. A pair missing from the matrix raises
   `METRO_FARE_UNKNOWN`. For a ride across several stations, price it once with
   `calculate_metro_fare(origin, destination)` instead of summing segment legs.
3. **Explicit fare row** for (mode, origin, destination; reverse direction accepted) and the scenario's
   column pair. Used as-is.
4. **Formula**, when there is no row or the row has no value for that scenario:
   `max(base + per_km × km, min)` → × low/high band → × scenario multiplier → × the matching row's
   `Area_Wealth_Multiplier` (non-metro, numeric only) → minimum fare applied again.
   Distance: `road_distance_km` → `straight_line_km × 1.35` → the fare row's distance → hub coordinates
   (Haversine × 1.35) → `DISTANCE_UNKNOWN` error (never guessed).
   A `fare_rules.allow_formula_fallback = false` mode raises `FORMULA_FALLBACK_DISABLED`.

Scenarios: `normal, rain, peak, peak_plus_rain, detour, regular_holiday, festive_holiday`
(aliases `heavy_rain`→`rain`, `peak_rush_hour`→`peak`).

## Decisions you should know about

- **Wealth multiplier** is the fare row's explicit `Area_Wealth_Multiplier` only. It is never derived from hub
  wealth indices, and it is not re-applied to explicit fares (those already include it).
  A formula fare with no matching row gets no wealth adjustment (a warning says so).
- **`peak_plus_rain` multiplier** is null in the dataset. The formula path derives it as `rain × peak`
  (the dataset's explicit `peak_plus_rain` fares are approximately that) and adds a note. Explicit rows take
  priority, so this only affects the formula fallback.
- **Student fare** (`calculate_student_fare`) follows `fare_rules`. Default: `local_bus` 50%, minimum 10, via
  `max(min, ceil(regular × 0.5))`; every other mode (rickshaw, CNG, metro…) gets no discount.
  Explicit `Student_Fare_*` columns are used only when the discount applies and the scenario is `normal`
  (they are normal-scenario values). Otherwise the rule is applied to the scenario's regular fare.
  **Dataset caveat:** in 8 of 105 bus rows the explicit student fare differs from the rule (for example a
  Tk 21 fare has an explicit student fare of Tk 10, where the rule gives 11). The explicit value wins.
- **`prefer_student=False`** makes the student fields mirror the regular fare with
  `student_discount_applies=false`. With `True` they hold the student fare.
- **Rounding** of student amounts uses `ceil`; the float-noise guard prevents 30.000000000000004 → 31.
- **Ride-share**: `calculate_rideshare_split(total_range, n)` gives per-person min/max plus
  `suggested_safe_per_person_bdt = ceil(per_person_max)`. This is a safe upper bound, not a tariff.
- **Sanity check** (`validate_submitted_fare`): flags a negative fare, or a fare above 5× the formula's
  expected max (for the scenario). For metro it compares with the official matrix. When data is missing
  (no distance, unknown stations) it reports `checked=false` instead of flagging. It never blocks; callers
  set `needs_admin_review` from the result.
- All non-metro fares carry the "estimates for planning/demo, not official tariffs" note.

## Against the seeded database

Of 968 route edges, 955 get fares in every scenario: 903 from explicit rows, 36 from the formula (bus
segments from the co-ordination dataset, using hub coordinates), 16 from the metro matrix. The other 13 are metro
segments between stops that are not in the official matrix (for example Cantonment → Mirpur 12), which fail
with `METRO_FARE_UNKNOWN`. Routing must price metro rides station-to-station.
