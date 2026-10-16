# DhakaPath — Live Urban Transit & Route Intelligence

Multi-modal transit, fare and live street-alert platform for Dhaka, built for **IAIT National IT Fest 2026 (INIF) — Smart City**.

Commuters search trips in English, Banglish or Bangla, see fares (regular vs student, ranges for rickshaw/CNG), report and confirm street disruptions, simulate rain / rush hour / blocked intersections with automatic detours, and chat with an assistant. Admins moderate reports, block roads and verify users.

- **Frontend:** React 19, Vite, TypeScript, MUI, TanStack Query, Zustand, Leaflet + OpenStreetMap (no paid map API)
- **Backend:** FastAPI, SQLModel, SQLite (PostgreSQL in production), JWT auth
- **AI:** optional free-tier LLM (OpenRouter, Gemini or Groq). With no key, a built-in rule-based parser handles chat, so the app works fully offline.

## Features

| Problem-statement part | What is implemented |
|---|---|
| 1. Transit & street-status CMS | Hubs, routes, base fares, student eligibility, boarding notes. Admin dashboard: moderate reports, block/unblock roads, edit fare rules, verify users. |
| 2. Multi-constraint trip search | Natural-language search (English, Banglish, Bangla script), budget, avoid-hub, optimize by cheapest / fastest / fewest transfers, multi-leg results with transfers. |
| 3. Fare estimation & simulation | Regular vs student fare breakdown, rickshaw/CNG fare ranges, rain / rush-hour / blocked-intersection scenarios with broken segments, detour plan and before/after cost and time. |
| 4. Validation & grounding | AI route answers are checked against the transit graph. Reports decay and expire unless re-confirmed, and unrealistic fares and contradictory reports are flagged for admin review. |
| 5. Conversational assistant | Chat for routes, fares, disruption reports (draft, then confirm), alternative routes and follow-up questions, in English, Banglish and Bangla. |
| Bonus | Signboard / notice photo digitization (needs `GEMINI_API_KEY`) and a hub ride-share board with a per-person fare split bot (`/community`). |

## Repository layout

```
backend/    FastAPI app (app/), datasets (co-ordination/, *.csv), config (dataset_config/), docs/, tests
frontend/   React app (src/): pages, features/{map,trip-search,simulator,alerts,chat,reports}, api/
render.yaml          Render Blueprint (backend + database)
DEPLOY_RENDER.md     Render deploy guide
DEPLOY_VERCEL.md     Frontend on Vercel
```

More detail: [backend/README_BACKEND.md](backend/README_BACKEND.md) and [backend/docs/](backend/docs/) (architecture, routing engine, fare engine, simulation, grounding, chat).

## Run locally

Requires Python 3.11+ and a recent Node.js (with npm).

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env

# Build the real Dhaka dataset and seed the SQLite database
python -m app.scripts.normalize_datasets
python -m app.scripts.seed --source normalized --reset

uvicorn app.main:app --reload --port 8000
```

API: http://localhost:8000 — interactive docs: http://localhost:8000/docs — health: `/health`.

### 2. Frontend

```bash
cd frontend
copy .env.example .env            # macOS/Linux: cp .env.example .env
npm install
npm run dev
```

App: http://localhost:5173. `VITE_API_BASE_URL` in `frontend/.env` must match the backend port (default `http://localhost:8000`). Restart `npm run dev` after changing it.

### Demo accounts (seeded)

| Email | Password | Role |
|---|---|---|
| `admin@dhakapath.local` | `password123` | admin |
| `user1@dhakapath.local` | `password123` | verified user |
| `user2@dhakapath.local` | `password123` | verified user |

Accounts created through **Register** start unverified. An admin must verify them (Dashboard → Users) before they can report, vote or run simulations. Guests can browse and search.

### Tests

```bash
cd backend
pytest -q          # full suite, uses throwaway databases, no network or LLM key needed
```

## Configuration

Backend `.env` (see `backend/.env.example`):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `sqlite:///./data/dhakapath.db` locally, PostgreSQL URL in production |
| `JWT_SECRET` | Token signing key — set a long random value in production |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins |
| `OPENROUTER_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` | Optional. Leave blank to use the rule-based chat parser |
| `DEMO_SECRET` | Guards debug/grounding demo endpoints |

Frontend `.env`: `VITE_API_BASE_URL`, `VITE_USE_MOCKS=false`, `VITE_EVENT_POLL_INTERVAL_MS=12000`.

## How it works (short)

- **Routing:** the transit graph (hubs and route edges) is rebuilt from the database on every search, so admin road blocks, verified reports and simulations take effect immediately. Results are ranked by cost, duration and transfers. See `backend/docs/ROUTING_ENGINE.md`.
- **Fares:** explicit fare rows and the official metro matrix first, then a per-mode distance formula for non-metered modes, returned as min–max ranges. Students get 50% off buses (10 BDT floor); metro and other modes without a student pass are not discounted. See `backend/docs/FARE_ENGINE.md`.
- **Alerts:** a report moves `pending → verified | rejected → resolved | expired`. Only an admin verification can change routing; votes and confirmations adjust confidence, and confidence decays over time. See `backend/docs/REPORTS_API.md`.
- **Grounding:** every route, hub and fare an AI reply mentions is checked against the database, and replies that fail are replaced with data from the engine. See `backend/docs/GROUNDING.md`.
- **Data:** route geometry and stops come from `backend/co-ordination/` (metro and bus route maps, Dhaka road network) and fares from the `Dhaka_*.csv` files. Raw files are never modified. Some hubs in the fare sheet have no coordinates in the data, so the map draws them at approximate positions, marked "approx. location" in the hub popup (`frontend/src/features/map/hubs.ts`).

## Deployment

- **Backend:** Render, via [render.yaml](render.yaml) and [DEPLOY_RENDER.md](DEPLOY_RENDER.md). Start command: `bash render_start.sh`, which builds the datasets, seeds the database and starts uvicorn.
- **Frontend:** Vercel or Render static site. Set `VITE_API_BASE_URL` to the backend URL and redeploy, since Vite bakes it in at build time. See [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md).
- Set the backend `CORS_ORIGINS` to the deployed frontend URL.

## Known limitations

- Non-metro fares are planning estimates, not official tariffs. Responses carry a warning saying so.
- The dataset flags no flood-prone roads, so the "heavy rain" scenario changes fares and times but closes no roads.
- Uploaded report photos are stored on local disk, which is ephemeral on Render's free tier.
- The Render free tier sleeps after 15 minutes idle, and the first request afterwards takes about 30–60 s.
