# Dataset Contract

No data is invented. Raw files are never modified. Normalized output goes to `backend/data/generated/`
(see `DATASET_SCHEMA_MAP.md` for every column → field mapping). If a file is missing, the pipeline
records the gap and writes empty output; it does not fail.

Fare and rule files may sit directly under `backend/` or anywhere under `backend/datasets/`, as `.csv`,
`.tsv` or `.xlsx`. They are recognised by their headers, not their filenames.

## A. Dhaka_Transport_Fares (delivered: `backend/Dhaka_Transport_Fares.csv`, 918 rows)
Expected columns:
Mode, Category, Provider, From_Hub, To_Hub, Road_Distance_km, Time_Normal_min, Time_Peak_Hour_min,
Time_Rain_min, Metro_Stations, Wealth_Index_From, Wealth_Index_To, Area_Wealth_Multiplier, Area_Wealth_Note,
Normal_Low_BDT, Normal_High_BDT, Rain_Low_BDT, Rain_High_BDT,
Peak_Office_Hour_Low_BDT, Peak_Office_Hour_High_BDT, Peak_Plus_Rain_Low_BDT, Peak_Plus_Rain_High_BDT,
Blockage_Construction_Waterlog_Detour_Low_BDT, Blockage_Construction_Waterlog_Detour_High_BDT,
Regular_Holiday_Low_BDT, Regular_Holiday_High_BDT, Festive_Holiday_Low_BDT, Festive_Holiday_High_BDT,
Student_Fare_Low_BDT, Student_Fare_High_BDT, Student_Rule, Notes

What the delivered file contains:
- All 32 columns, plus a first column headed `Dhaka_Metro_Fare_Matrix` that holds row numbers 1..918.
- 15 hubs and 105 hub pairs, each listed in one direction only.
- 10 modes, including `MRT-6 Metro Rail` (15 rows, which agree with the official matrix).

## B. Dhaka_Metro_Fare_Matrix (delivered: `backend/Dhaka_Metro_Fare_Matrix.csv`)
A 10 × 10 station matrix of fares in BDT. Stations: Uttara North, Pallabi, Mirpur 11, Mirpur 10, Kazipara,
Agargaon, Farmgate, Shahbagh, Dhaka Univ., Motijheel.

## C. Section rules (delivered: `backend/Dhaka_Fare_Assumptions.csv`)
Layout `Section,Item,Value,Meaning`. It contains:
- 15 hub wealth indices
- 9 mode rates
- 9 multiplier rows
- 9 rules: road distance, peak hours, rain, detour, holidays, student, metro, disclaimer

The same content is in `dataset_config/problem_owner_rules.json` (the problem owner's brief), which is
used as a fallback and as a cross-check.

## D. Official route and co-ordination dataset — `backend/co-ordination/`
This dataset is authoritative for route topology, hub coordinates, route segments, geometry, stops,
direction and travel time (when available).

Delivered: 4 headerless CSVs, read through `dataset_config/coordination_mapping.json`.

| File | Layer | Rows | Content |
|---|---|---|---|
| Roadmap-Dhaka.csv | DhakaStreet | 49,869 | road polylines; trailing `0`, `cost` |
| Routemap-BikolpoBus.csv | DhakaBusBikolpo | 17 | stop-to-stop bus segments, `from, to` names |
| Routemap-UttaraBus.csv | DhakaBusUttara | 19 | stop-to-stop bus segments |
| Routemap-DhakaMetroRail.csv | DhakaMetroRail | 14 | stop-to-stop metro segments |

Known gaps and ambiguities. These are recorded, not fixed:
- There is no explicit hub table. Hub coordinates come only from route-geometry endpoints.
- There is no direction, one-way, status or travel-time field. Edges are therefore marked
  `direction_unknown: true`.
- Geometry vertex order often runs opposite to the listed from→to; orientation is inferred.
- Stop names are spelled inconsistently (Firmgate, Shahbag, Matijhil, DU, Sonarga hotel). These are
  proposed as aliases, not merged.
- Metro rows 3–5 carry labels that contradict their shared endpoints ("Cantonment → Mirpur 12" between
  two "Mirpur10" ends). Those points are excluded from hub coordinates.
- What the road-network trailing values mean is unconfirmed.

A later delivery with headered tables (stops with lat/lng, edges with from/to/one_way/status/geometry)
is supported without code changes. Column names are matched through synonyms.
