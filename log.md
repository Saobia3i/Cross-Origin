# DhakaPath — Developer Handoff Log

Last integration update: 2026-10-16 (branch `co-ordinate-dataset`). Practical notes for teammates, not marketing.

## 1. Project snapshot

| Area | State |
|---|---|
| Backend (`backend/`) | FastAPI + SQLModel + SQLite, fully written. 653 pytest tests pass (verified). Unified envelope on every response. |
| Datasets | Real fare / metro matrix / assumptions CSVs under `backend/`, route data under `backend/co-ordination/`. Normalizer + seeder run against them (verified). |
| Frontend (`frontend/`) | React 19 + Vite 8 + TS + MUI + TanStack Query + Zustand + i18next scaffold. **API layer, stores and feature components were rewired to the real backend contract in this update.** |
| Map | **Not built.** `HomePage` and `SimulatorPage` still show a placeholder. Leaflet / react-leaflet are installed and `leaflet.css` is imported. |
| MSW mocks | **Do not exist.** `msw` is a devDependency but there are no handlers; `VITE_USE_MOCKS` is reserved and unused. |

**Verified by Claude (this session)**
- `pytest -q` in `backend/`: 653 passed.
- `inspect_datasets` → `normalize_datasets` → `seed --source normalized --reset` on the real datasets (62 hubs, 968 route edges, 918 fare rows, 90 metro matrix rows). `seed --source fixtures --reset` also works.
- Live backend: `/health`, `/api/catalog`, `/api/transit/network`, `/api/trips/search`, `/api/auth/login|register|me`, `/api/chat`, `/api/simulations` (+`reset`), `/api/reports`, `/api/events`, `/api/fares/estimate` returned the documented shapes.
- CORS preflight from `http://localhost:5173` to the backend succeeds.
- Vite dev server starts and serves the changed modules (HTTP 200, no transform errors).

**Not verified**
- No browser session was driven: the UI was **not clicked through** against the live backend. Treat the new frontend wiring as untested end to end.
- `npm run build` fails (`tsc -b`) — **pre-existing**: the scaffold had ~60 type errors before this update (MUI v9 prop drift like `PaperProps` / `InputLabelProps` / `fontWeight` on `Typography`, unused imports). 49 remain; none were introduced by the integration (Navbar `superadmin` comparison was introduced and fixed). `vite` dev mode is unaffected.
- LLM providers (no API keys; chat used the rule-based parser).
- Image upload on reports, ride-share, event polling in a browser.

## 2. Expected repository layout

```
backend/                   FastAPI app (app/main.py), run from here
  app/api_catalog.py       endpoint source of truth (served at GET /api/catalog)
  app/scripts/             inspect_datasets, normalize_datasets, seed, reset_db, demo_logs
  co-ordination/           official route CSVs (raw, immutable)
  Dhaka_*.csv              raw fare / metro matrix / assumptions CSVs (immutable)
  dataset_config/          aliases and routing config (human-edited JSON)
  data/generated/          normalized artifacts (generated, git-ignored, never hand-edit)
  data/dhakapath.db        SQLite DB (git-ignored)
  data/uploads/            report photos (git-ignored)
  tests/fixtures/mini_datasets/   TEST_FIXTURE data, never production
frontend/                  Vite app (src/api, src/store, src/features, src/pages)
log.md                     this file
```
`backend/csv` does not exist (already `backend/co-ordination`); nothing was moved.

## 3. Prerequisites
- Python 3.11+ (3.13.0 used here), Node 20+ (v24.18.0 used here), npm (`package-lock.json` present).
- ffmpeg / image tooling: **not used** by any current code.

## 4. Quick start

Backend (from `backend/`; Windows paths shown, use `source .venv/bin/activate` on macOS/Linux):
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                      # then change JWT_SECRET / DEMO_SECRET
python -m app.scripts.inspect_datasets       # scan raw files
python -m app.scripts.normalize_datasets     # writes data/generated/normalized_*.json
python -m app.scripts.seed --source fixtures   --reset   # demo/dev only: synthetic TEST_FIXTURE rows
python -m app.scripts.seed --source normalized --reset   # real datasets
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Stop the server before `--reset`.

Frontend (from `frontend/`):
```bash
npm install
copy .env.example .env
npm run dev          # http://localhost:5173
```
Demo event script (backend running on a seeded DB, second terminal, from `backend/`):
```bash
python -m app.scripts.demo_logs --base-url http://localhost:8000 --session DEMO_SESSION --interval 3
```
Seeded logins (password `password123`): `admin@dhakapath.local`, `user1@dhakapath.local` (verified), `user2@…`, `user3@…`. New registrations are **not** verified.

## 5. Dataset modes
- **Fixture mode** (`--source fixtures`): only `backend/tests/fixtures/mini_datasets/` (marked `TEST_FIXTURE`; 3 hubs, 3 edges, 2 reports). For dev/demo/tests only.
- **Normalized real mode** (`--source normalized`): reads `backend/data/generated/normalized_*.json`. Current run: 62 hubs, 968 route edges, 918 fare rows, 90 metro matrix rows, 9 mode rates, 63 multipliers, 15 wealth indices, 12 fare rules, 0 reports.
- **Missing datasets:** seed does not fabricate transit data; only demo users and default fare rules are created.
- **Raw files are immutable.** Aliases applied: `Uttara → Uttara North`, `Mirpur → Mirpur 10`. Other proposed aliases (e.g. `Shahbag ~ Shahbagh`, `Farmgate ~ Firmgate`, `Matijhil ~ Motijheel`) are NOT applied; approve them in `backend/dataset_config/hub_aliases.json` → `approved_aliases`, then re-run `normalize_datasets`.
- Fares: metro matrix is official (no weather/blockage multiplier, no student discount). Non-metro fares are estimates (explicit rows first, formula fallback; rickshaw/CNG ranges preserved; wealth multiplier only on non-metro modes where the dataset has it).

## 6. Environment variables

Backend `backend/.env` (template: `.env.example`, which already lists all of these):
`DATABASE_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CORS_ORIGINS` (default includes `http://localhost:5173`), `DEMO_SECRET`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `LLM_PROVIDER`, `LLM_CACHE_ENABLED`, `UPLOAD_DIR`, plus optional model / timeout overrides.

Frontend `frontend/.env` (template: `.env.example`):
`VITE_API_BASE_URL=http://localhost:8000` (legacy `VITE_API_URL` still read as fallback), `VITE_USE_MOCKS=false` (reserved), `VITE_EVENT_POLL_INTERVAL_MS=12000`.

Secrets: never commit `.env` (`backend/.env` and now `frontend/.env` are git-ignored). Keys are optional; without them chat uses the rule-based parser. `JWT_SECRET` / `DEMO_SECRET` must be changed outside local dev.

## 7. API contract rules
- **Envelope** on every response: `{ ok, data, meta{request_id,timestamp,source:"dhakapath-backend"}, errors[] }`. Frontend `api/client.ts` unwraps to `data` and throws `ApiError` (carries `errors[]`, `.code`).
- **`GET /api/catalog` is the endpoint source of truth** (`backend/app/api_catalog.py`). `frontend/src/api/endpoints.ts` is hand-aligned with it; re-check after backend changes.
- **Auth:** `Authorization: Bearer <access_token>` (login/register return `data.access_token` + `data.user`). Guests: browse/search/public alerts. Verified users: report, vote, confirm, resolve, simulate, ride-share. Admin: verify/reject/reopen.
- **`session_id`:** generated once and persisted (`store/session.ts`, localStorage `dhaka_session`); sent on trip search, fare estimate, chat, simulations, report create, events.
- **Fares are ranges:** `total_regular_fare_min/max_bdt`, `total_student_fare_min/max_bdt`; per leg `regular_fare_*`, `student_fare_*`, `student_discount_applies`. `FareText` renders `৳min–max` when they differ. Never show a single fake number for rickshaw/CNG.
- **Student fare:** local bus 50% discount, minimum 10 BDT (backend engine applies it; UI only displays). When no discount applies, the student range equals the regular range. Metro is never discounted.
- **Report statuses:** `draft, pending, verified, rejected, resolved, expired`. Only verified, non-expired reports affect routing. Admin verify/reject is final; votes only move confidence/community score. Chat disruption reports create a **draft**; the user confirms via `POST /api/reports/drafts/{id}/submit` → `pending`.
- **Simulation is per session** (`session_id`), never global. Admin road block/unblock is global but those endpoints are not implemented yet (501).
- **Events:** `GET /api/events?since_id=&session_id=&limit=100`, cursor = returned `next_since_id`. `hooks/useEventPolling.ts` polls every `VITE_EVENT_POLL_INTERVAL_MS` and invalidates `alerts` / `network` queries on `report.*` and `route.recalculated`.
- **Grounding:** backend engines are the source of truth; the grounding validator blocks hallucinated routes/fares. UI shows `grounding.valid` badges.
- **Chat:** read `reply_text` (not `reply.text`), plus `intent`, `entities`, `structured_payload`, `grounding`, `context_state`. If `structured_payload.routes` is present the chat drawer pushes it into the trip store.

## 8. Working with the existing backend (frontend devs)
1. Seed + run the backend (fixtures if you only need quick data), open `http://localhost:8000/docs` and `/api/catalog`.
2. Set `frontend/.env`; `npm run dev`.
3. **Mocks:** none exist. `VITE_USE_MOCKS` currently does nothing. If you add MSW, handlers must return the unified envelope and be labelled test-only; do not invent Dhaka hubs/fares.
4. Required request fields: trip search `origin`, `destination` (names or aliases; backend resolves) + `session_id`, optional `budget_bdt`, `prefer_student_fare`, `avoid_hubs`, `optimize` ∈ `cheapest|fastest|fewest_transfers|balanced`. Simulation: `session_id`, `scenario_type` ∈ `heavy_rain|peak_rush_hour|blocked_intersection|regular_holiday|festive_holiday|detour`. Report: `report_type`, `severity` ∈ `light|moderate|heavy`, `description`, optional `affected_hub` (hub id or name), `incident_time` (not future, ≤24h old).
5. Rendering: routes → `route_option_id`, `rank_label`, ranges, `legs[]` (`from_hub`/`to_hub` are hub IDs, e.g. `UTTARA_NORTH`; look up names from `/api/transit/network`). Show `warnings[]` (includes "Non-metro prices are estimates…").
6. **Map props contract (for whoever builds it):** `hubs, routes, alerts, selectedRoute, alternativeRoute, closedRouteIds, highlightHubIds, theme, layers`; callbacks `onHubClick, onAlertClick, onMapClick, onRouteClick`. Hub `latitude/longitude` can be `null` — 16 hubs have no coordinates in the real data (see gaps), so use the schematic fallback for those.
7. Debug checklist: backend `/health` OK? Network tab shows envelope with `ok:true`? `Authorization` header present after login? `session_id` in body? Check `errors[0].code` (e.g. `NOT_VERIFIED`, `ROUTE_OPTION_NOT_FOUND`, `VALIDATION_ERROR`).

## 9. Feature integration checklist

Legend: ✅ wired to backend (not browser-tested) · ⚠ partial · ❌ missing

| Feature | Status | Notes |
|---|---|---|
| Part 1 CMS: network, hubs, routes view | ✅ | `NetworkPage` uses `/api/transit/network`. |
| Part 1 CMS: reports list / vote / admin verify-reject | ✅ | `AlertsFeed`, `AdminPage`. Comments UI removed (no backend endpoint). Admin "applications" queue removed (no backend endpoint). |
| Part 1 CMS: road block/unblock, route/hub/fare-rule editing | ❌ | Backend returns 501 for `/api/admin/routes*`, `/hubs*`, `/fare-rules*`; no UI. |
| Part 2 trip search | ✅ | `TripSearchForm` (hub names from network), `RouteResultsList`, `RouteDetail`. |
| Part 3 fare ranges + simulation | ✅ | Ranges everywhere; `SimulatorPanel` uses `before_after_comparison`. Needs a verified login. |
| Part 4 validation / grounding | ✅ | `grounding.valid` badges. No UI for `/api/grounding/*` (debug, needs `DEMO_SECRET`). |
| Part 5 chat | ✅ | `ChatDrawer`: `reply_text`, draft confirm/cancel buttons. |
| Bonus ride-share | ❌ | Backend ready (`/api/rideshare/posts*`); no frontend. |
| Bonus audio / offline card | ❌ | Not started. |
| Bonus signboard OCR | ❌ | Not started. |
| Event polling | ✅ | `useEventPolling` mounted in `App`. |

## 10. Bonus next-development plan
1. **Map component (highest demo value).** Frontend: Leaflet/OSM (`react-leaflet`) with the props above; schematic node graph fallback for hubs without coordinates. Acceptance: selected route polyline highlights, closed routes red, hub click fills origin/destination. Demo: simulate rain/blockade and watch the reroute.
2. **Ride-share UI.** Backend exists; frontend: list/create/join/leave/cancel. Acceptance: shows `per_person_min_bdt`–`per_person_max_bdt` and `suggested_safe_per_person_bdt = ceil(max)`, labelled estimate not tariff.
3. **MSW mocks** (`VITE_USE_MOCKS=true`) using the envelope and clearly `TEST_FIXTURE`-labelled data.
4. Backend: implement admin route/hub/fare-rule endpoints (currently 501), add `localhost` ports if frontend moves.
5. Audio/offline card, signboard OCR: only after 1–3.

## 11. Demo runbook
```bash
# terminal 1 (backend/)
python -m app.scripts.seed --source normalized --reset
uvicorn app.main:app --host 0.0.0.0 --port 8000
# terminal 2 (frontend/)
npm run dev
# terminal 3 (backend/), optional scripted demo
python -m app.scripts.demo_logs --base-url http://localhost:8000 --session DEMO_SESSION --interval 3
```
Story: guest searches Uttara → Motijheel (metro 100 BDT fixed vs local bus estimate, student toggle halves the bus fare) → log in as `user1@dhakapath.local`, submit a waterlogging report → log in as admin, verify it → route recalculation event arrives within ~12 s → run the rain simulation (per session) → ask the chat for an alternative route → grounding fake-test via `demo_logs`.
Fallbacks: **LLM fails** → backend falls back to the rule-based parser, chat still answers from the engines. **Dataset missing** → `seed --source fixtures --reset` (3 hubs; say it is test data). **Map tiles fail** → use the schematic fallback (map not yet built).

## 12. Troubleshooting
- **CORS:** add the frontend origin to `CORS_ORIGINS` in `backend/.env` and restart.
- **DB locked:** stop uvicorn before `seed --reset` / `reset_db`.
- **Missing dataset / no routes:** run `normalize_datasets` then `seed --source normalized --reset`; check `data/generated/normalization_report.json`. If a hub is not recognised, try the canonical name (Uttara North, Mirpur 10) or check `hub_aliases.json`.
- **Fare range confusion:** `min != max` is intended (estimates). Metro is a single value.
- **Student fare confusion:** `student_discount_applies=false` ⇒ student range equals regular range. Bus 50%/min 10 BDT only.
- **Report not affecting routing:** it must be `verified` by an admin, not expired, confidence ≥ 0.40, and have `affected_hub` or route ids; `edge_closed`/`extra_time_min` only if the admin approved them.
- **Simulation affecting the wrong session:** every call must send the same `session_id`; clearing localStorage creates a new session.
- **Simulation rejected:** the catalog marks simulations `verified_user`; registered accounts are not verified automatically (exact error code not tested).
- **Chat LLM failure:** unset keys or provider timeout → rule-based fallback; check `LLM_TIMEOUT_S`.
- **Event polling failure:** check `/api/events?since_id=0`; the hook swallows errors and retries each interval.

## 13. Merge rules
- Backend owns the API contract (`api_catalog.py`, `docs/BACKEND_CONTRACT.md`); frontend adapts.
- Frontend owns the UI.
- Raw dataset files are immutable; generated artifacts in `backend/data/generated/` are never hand-edited.
- Update this file after any major integration.

## 14. Known gaps / TODOs
- No map component, no MSW mocks, no ride-share UI (see §9).
- **Frontend `tsc -b` fails (pre-existing, 49 errors).** Dev server works; production build is blocked until fixed.
- Admin road block/unblock, hub/route edit, fare-rule edit: backend 501, no UI. Admin "applications" and report comments: no backend endpoints, UI removed.
- 16 real hubs have no coordinates (BADDA, BANASREE, DHAKA_UNIV, DHANMONDI, GABTOLI, JATRABARI, MALIBAGH, MIRPUR_11, MOHAMMADPUR, MOTIJHEEL, PALLABI, SCIENCE_LAB, SHAHBAGH, SONARGAON_HOTEL, SONARGA_HOTEL, UTTARA_NORTH). Dataset owners must supply coordinates before a geographic map is meaningful.
- Duplicate hub ids `SONARGAON_HOTEL` / `SONARGA_HOTEL` and unapproved alias proposals (see §5).
- Metro leg 2 of multi-leg metro trips shows `0` fare with a `METRO_RIDE` warning (fare is charged on the first leg) — UI should explain it.
- `backend/docs/REPORTS_API.md` still says "no login/register endpoint yet" and "no draft endpoint"; both exist now (stale doc).
- Frontend i18n: new strings (errors, draft states) are English only.
- Route/hub names shown in alerts and legs are hub IDs; names need a lookup from `/api/transit/network`.

---

## Integration Update — 2026-10-16

Backend (no source changes): created local `backend/.env` from `.env.example` (git-ignored); ran inspect/normalize/seed; ran tests.

Frontend changes:
- `.env.example`: `VITE_API_BASE_URL`, `VITE_USE_MOCKS`, `VITE_EVENT_POLL_INTERVAL_MS`; local `.env` created; `.env` added to `frontend/.gitignore`.
- `src/api/client.ts`: env base URL, envelope unwrap, `ApiError` with `errors[]`, Bearer token.
- `src/api/schemas.ts`, `src/api/endpoints.ts`: rewritten to the real contract (ranges, `reply_text`, `access_token`, canonical statuses, `/api/reports/*`, `/api/admin/reports/*`, `/api/simulations*`, `/api/events`).
- New `src/store/session.ts` (persisted `session_id`), new `src/hooks/useEventPolling.ts` (mounted in `App`).
- `store/trip.ts`, `store/chat.ts`, `store/auth.ts` aligned (role `user|admin`, session id moved out of chat store).
- Components: `FareText` (min/max ranges), `ModePill` (backend mode ids), `StatusBadge` (canonical statuses), `TripSearchForm`, `RouteResultsList`, `RouteDetail`, `ChatDrawer` (draft confirm/cancel), `SimulatorPanel`, `AlertsFeed`, `ReportForm` (photos now optional, severity `light|moderate|heavy`), `AdminPage` (verify/reject), `AuthPages`, `NetworkPage`, `Navbar`.
- i18n: added `mode.local_bus`.

Decisions: kept the backend contract untouched; hub dropdowns now come from `/api/transit/network` instead of a hardcoded list; removed UI for endpoints that do not exist rather than faking them.
