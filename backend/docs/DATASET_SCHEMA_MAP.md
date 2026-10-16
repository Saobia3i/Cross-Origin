# Dataset Schema Map

How raw dataset columns become the normalized JSON files in `backend/data/generated/`.
Code: `app/services/dataset_loader.py` (read and inspect), `dataset_normalizer.py` (transform),
`hub_alias.py` (names) and `dataset_manifest.py` (kinds and paths). Config: `backend/dataset_config/`.

```
python -m app.scripts.inspect_datasets      # -> data/generated/dataset_manifest.json
python -m app.scripts.normalize_datasets    # -> data/generated/normalized_*.json + normalization_report.json
```

## 1. Sources and detection

| Kind (manifest `detected_kind`) | Where it is looked for | Detected by |
|---|---|---|
| `dhaka_transport_fares` | `backend/*`, `backend/datasets/**` | headers `mode`, `from_hub`, `to_hub` plus at least 2 `*_bdt` columns, or at least 8 of the 32 expected columns |
| `dhaka_metro_fare_matrix` | same | at least 3 known metro stations (after aliases) in the header row or first column; or a long from/to/fare table with "metro" in its name |
| `section_rules` | same | rules keywords (wealth, multiplier, base, /km, festive, detour); title-row or `Section` column layout |
| `coordination_polyline` | `backend/co-ordination/**` | headerless rows: text layer, then numeric pairs, then trailing fields |
| `coordination_edges` / `coordination_hubs` | `backend/co-ordination/**` | headered tables with from/to columns, or with lat/lng columns |

Headers decide the kind; filenames are only tie-break hints. `.csv`, `.tsv` and `.xlsx` are read, and each
Excel sheet is inspected as its own dataset. `.xls` is reported as unsupported and is not read.
Delimiters are sniffed. Encodings tried, in order: utf-8-sig, cp1252, latin-1.

**Header normalization:** headers become snake_case (`Road_Distance_km` → `road_distance_km`,
`From / To Station (BDT)` → `from_to_station_bdt`, `fromHub` → `from_hub`). Duplicate headers get `_2`.
The original names are kept per file in `normalization_report.json → column_mappings`
(`{normalized: original}`).

## 2. Dhaka_Transport_Fares → `normalized_fare_rows.json` (+ edges)

| Original column | Normalized field |
|---|---|
| Mode | `mode` (raw label) and `mode_code` (via `dataset_config/mode_aliases.json`) |
| Category, Provider | `category`, `provider` |
| From_Hub, To_Hub | `from_hub_name`/`to_hub_name` (raw) and `from_hub_id`/`to_hub_id` |
| Road_Distance_km | `road_distance_km` |
| Time_Normal_min, Time_Peak_Hour_min, Time_Rain_min | `time_normal_min`, `time_peak_hour_min`, `time_rain_min` |
| Metro_Stations (`"Motijheel -> Shahbagh"`) | `metro_stations` [list], `metro_station_hub_ids` |
| Wealth_Index_From/To, Area_Wealth_Multiplier, Area_Wealth_Note | `wealth_index_from/to`, `area_wealth_multiplier`, `area_wealth_note` |
| Normal_Low/High_BDT | `fares.normal.{low_bdt, high_bdt}` |
| Rain_* | `fares.rain` |
| Peak_Office_Hour_* | `fares.peak` |
| Peak_Plus_Rain_* | `fares.peak_plus_rain` |
| Blockage_Construction_Waterlog_Detour_* | `fares.detour` |
| Regular_Holiday_*, Festive_Holiday_* | `fares.regular_holiday`, `fares.festive_holiday` |
| Student_Fare_* | `fares.student` |
| Student_Rule, Notes | `student_rule`, `notes` |
| every column | `raw` (snake_case keys, numbers parsed) |
| first column `Dhaka_Metro_Fare_Matrix` in the delivered file | holds the sequential row numbers 1..918, so it becomes `source_row_id`, not data (a warning is written) |

Each record also has `fare_row_id` (`FR_0001`…, in file order), `route_id`, `route_match`
(`same_direction` / `reverse_direction` / `created_from_fare_row`), `superseded_by_metro_matrix` and
`metro_matrix_fare_bdt` (on metro rows).

## 3. Dhaka_Metro_Fare_Matrix → `normalized_metro_fare_matrix.json`

Each off-diagonal cell becomes `{from_station, to_station, from_hub_id, to_hub_id, fare_bdt, mode: "metro",
mode_code: "MRT6", source: "Dhaka_Metro_Fare_Matrix", official: true, affected_by_weather: false,
affected_by_blockage: false, wealth_multiplier_applicable: false, student_discount_applicable}`.
`student_discount_applicable` is true only if a dataset rule grants a metro student discount; the current
rules say "bus only", so it is false. Diagonal cells and empty cells are skipped, and asymmetric pairs are
warned about.

## 4. Section rules (`Dhaka_Fare_Assumptions.csv`)

Two layouts are supported:
- **Section column**, e.g. `Section,Item,Value,Meaning`. This is the delivered file.
- **Title rows**, e.g. a row `Mode rates` followed by data rows (one text line per row, or a columnar table).

| Section | Output | Fields |
|---|---|---|
| Hub wealth index | `normalized_wealth_indices.json` (`kind: hub_wealth_index`) | `hub_name`, `hub_id`, `wealth_index`, `note` (Meaning column) |
| (fare rows) | same file (`kind: fare_row_area_wealth_multiplier`) | `fare_row_id`, `mode_code`, `from/to_hub_id`, `wealth_index_from/to`, `area_wealth_multiplier`, `area_wealth_note` |
| Mode rates | `normalized_mode_rates.json` | `mode`, `mode_code`, `base_fare_bdt`, `per_km_bdt`, `min_fare_bdt`, `average_speed_kmh`, `low_band_multiplier`, `high_band_multiplier`, `raw_text` |
| Multipliers | `normalized_multipliers.json` | one record per (mode, scenario) for `normal, rain, peak, peak_plus_rain, detour, regular_holiday, festive_holiday`: `multiplier`, `basis`, `note` |
| Rule | `normalized_rules.json` | `key`, `label`, `value`, `params` |

- **Multiplier basis:** `source` (stated), `implicit_baseline` (normal = 1.0) or `not_specified_in_source`
  (multiplier `null`). `peak_plus_rain` is not stated anywhere, so it is null. The fare engine must decide
  how to combine peak and rain; it must not be inferred here.
- **Rule params:** the `params` extracted from rule text are `road_distance.straight_line_multiplier`
  (1.35); `peak_hours.windows` (08–10, 17–20) and `days`; `detour.extra_distance_pct` (35) and
  `refusal_premium`; `student.discount_pct` (50), `applies_to_mode_codes` ([LOCAL_BUS]) and `min_fare_bdt`
  (10); `metro.fare_source`, `affected_by_*`; `disclaimer.non_metro_prices_are_estimates` and
  `declared_aliases`.
- **Brief fallback and cross-check:** `dataset_config/problem_owner_rules.json` holds the problem owner's
  brief verbatim. For a production run it fills in any rules section that no file provides, and it
  cross-checks the file. The delivered file matches the brief exactly; mismatches would appear in the
  report's `warnings`.

## 5. Official co-ordination dataset (`backend/co-ordination/`)

The four delivered CSVs have **no header**. The layout is configured in `dataset_config/coordination_mapping.json`:

```
<layer>, x1, y1, x2, y2, ..., <trailing fields>
```

| Layer | Role | Trailing fields | Output |
|---|---|---|---|
| `DhakaStreet` (Roadmap-Dhaka.csv, 49,869 rows) | road network | `attr_flag_raw` (always 0), `attr_cost_raw` (≈2.40 × length km; meaning unconfirmed) | `normalized_road_segments.json` |
| `DhakaBusBikolpo` | transit route, `LOCAL_BUS`, line "Bikolpo Bus" | `from_stop, to_stop` | `normalized_route_edges.json` |
| `DhakaBusUttara` | transit route, `LOCAL_BUS`, line "Uttara Bus" | `from_stop, to_stop` | same |
| `DhakaMetroRail` | transit route, `MRT6` | `from_stop, to_stop` | same |

- **Coordinate order:** lon, lat. This is checked against the data: every first value is above 90, so it
  cannot be a latitude.
- **Geometry:** stored exactly as provided, as a GeoJSON LineString. Consecutive duplicate points are
  collapsed, and `geometry_length_km` is computed from it.
- **Orientation:** in many rows the vertices run opposite to the listed from→to. `geometry_orientation`
  (`as_listed` / `reversed` / `unknown` / `ambiguous`) is inferred only from endpoints shared (within
  15 m) with the neighbouring rows of the same layer.
- **Label conflicts:** where two neighbouring rows meet at one point but name it differently (for example
  metro rows 3/4 "Mirpur10"/"Cantonment"), the conflict goes to `label_conflicts` and that point is not
  used as a hub coordinate.
- **Combined stop field:** a single field like `Farm gate. Karwan Bazar` is split on `.` into from/to,
  with a warning.

Headered co-ordination tables (none delivered yet) map columns via synonyms: `route_id`, `mode`, `from_hub`
(from/origin/source/from_stop), `to_hub` (to/destination/target/to_stop), `latitude`/`longitude`
(lat/lng/lon), `geometry` (WKT LINESTRING or GeoJSON; anything else is kept as `geometry_raw`),
`stops`/`via` (→ `via_hub_ids`), `distance_km`, `time_min`, `direction`, `one_way` and `status`.
Missing columns are left null.

## 6. Hub rules → `normalized_hubs.json`

- **`hub_id`:** UPPER_SNAKE of the display name. Letter/digit boundaries become word breaks.
  `Mirpur 10` → `MIRPUR_10`, `Mirpur10` → `MIRPUR_10`, `Uttara North` → `UTTARA_NORTH`,
  `Dhaka Univ.` → `DHAKA_UNIV`. Names with no ASCII letters get `HUB_<sha1[:8]>`.
- **Formatting-only variants** (case, spaces, punctuation) are the same hub: `Farm Gate` = `Farmgate`,
  `Sisu Mela` = `Sisumela`.
- **Display name:** the preferred name from `hub_aliases.json` (official metro station spellings), else
  the variant with the most word breaks, then alphabetical. It does not depend on the order rows are read.
- **Aliases:** required aliases (problem owner) `Uttara` = `Uttara North` and `Mirpur` = `Mirpur 10`.
  The dataset states the same thing ("Metro mapping: Uttara=Uttara North, Mirpur=Mirpur 10"), which the
  report records as `confirmed_by`.
- **Spelling variants are never merged automatically.** Shahbag/Shahbagh, Firmgate/Farmgate,
  Matijhil/Motijheel and Sonarga hotel/Sonargaon Hotel are listed in the report's
  `hub_alias.proposed_aliases`. Approve one by copying it into `approved_aliases` in `hub_aliases.json`.
  DU vs Dhaka Univ. is too dissimilar to be proposed; add it by hand if wanted.
- **`aliases`:** a JSON array of every other observed spelling plus the alias names.
- **`name_bn`:** taken from a co-ordination hubs table when present; otherwise null.
- **Coordinates:** an explicit lat/lng column (`coordinate_status: from_dataset`) wins. Otherwise the hub
  takes an observed endpoint of official route geometry. The pipeline picks the medoid of the majority
  cluster within 300 m (`derived_from_route_geometry` / `majority_of_route_geometry_endpoints`), so the
  value is always a point that exists in the data, never an average. Conflicting clusters give `conflict`
  plus `coordinate_candidates`; no evidence gives `missing`. **Coordinates are never invented.** Hubs
  known only from fares, matrix or rules (for example `UTTARA_NORTH`, `MOTIJHEEL`, `SHAHBAGH`) have
  null coordinates.

## 7. Route edges → `normalized_route_edges.json`

- **Priority:** co-ordination edges come first and are the authoritative topology. A fare row with the
  same `mode_code` and hub pair is attached to that edge (`fare_row_ids`); the reverse direction is used
  unless the edge is one-way. Otherwise the fare row creates its own edge (`source_dataset:
  dhaka_transport_fares`, `geometry: null`).
- **`route_id`:** `{MODE_CODE}_{ORIGIN_HUB_ID}_{DESTINATION_HUB_ID}`, e.g. `LOCAL_BUS_MIRPUR_10_FARMGATE`,
  `MRT6_MIRPUR_10_KAZIPARA`, `RICKSHAW_MOTIJHEEL_BANASREE`. Duplicates get `_2`, `_3`… in deterministic
  order: co-ordination before fares, then source file, then source row.
  Example: `MRT6_CANTONMENT_MIRPUR_12_2`.
- **Direction:** `one_way`/`direction` columns are respected. Without them, `direction_unknown: true` and
  `direction: null`; nothing is assumed. The delivered data has no direction columns.
- **Status:** `active` unless the source says closed/blocked/inactive/suspended (`status_raw` keeps the
  original).
- **Preserved fields:** mode, provider, category, line name, distance, times and notes. Fares stay on the
  linked fare rows.

## 8. Precedence

- **Metro fares:** the official `Dhaka_Metro_Fare_Matrix` wins. Fare rows with `mode_code` MRT6 are
  marked `superseded_by_metro_matrix`, and they are compared to the matrix (all 15 match). Metro is
  unaffected by weather or blockage, gets no wealth multiplier, and gets no student discount unless a
  rule grants one.
- **Non-metro fares:** use the explicit scenario ranges in `Dhaka_Transport_Fares` for that hub pair and
  mode. When no row exists, compute from the formula: `normalized_mode_rates` (base + per_km × road km,
  floored at min, then the low/high band) × `normalized_multipliers` (scenario) × area wealth. Road km
  comes from `road_distance.straight_line_multiplier` when only straight-line distance is known. All
  non-metro prices are estimates (disclaimer rule). The fare engine is not implemented in this step.
- **Student:** 50% off, bus only, minimum Tk 10 (`rules.student.params`).

## 9. Missing data behaviour

- A missing production dataset does not crash the run. Its outputs are written as `[]`, the report marks
  it `status: missing`, and a warning is printed.
- If every dataset file is missing, the run is in **contract mode**: `contract_mode: true` and
  `message: "production data unavailable ..."`. Rules may still come from the problem owner brief
  (`source: problem_owner_brief`).
- Missing columns become null fields. Required-column gaps are listed per file in
  `dataset_manifest.json → missing_required_columns`.
- Test fixtures (`backend/tests/fixtures/mini_datasets/`) are used only with `--source fixtures`, and that
  run writes to `data/generated/fixtures/`. A `*TEST_FIXTURE*` file that turns up among production files
  is skipped.
