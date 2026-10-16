# DhakaPath Backend

FastAPI + SQLModel + SQLite. Python 3.11+ (developed on 3.13). Everything lives in `backend/`.

## Create venv
```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

## Run backend
```bash
uvicorn app.main:app --reload
```
Check `http://localhost:8000/health`, `http://localhost:8000/api/catalog` and the interactive docs at `/docs`.
Every response uses the envelope `{ok, data, meta{request_id,timestamp,source}, errors[]}`.

## Seed the database
Fixtures (synthetic, marked `TEST_FIXTURE`; 3 hubs, 3 route edges, demo users, 2 reports) for demos and tests:
```bash
python -m app.scripts.seed --source fixtures --reset
```
Real datasets (never invented; if the normalized files are missing only demo users and default fare rules are seeded):
```bash
python -m app.scripts.normalize_datasets      # writes data/generated/normalized_*.json + normalization_report.json
python -m app.scripts.seed --source normalized --reset
```
`--reset` deletes the SQLite file first; stop the server before running it. Switching between `fixtures` and
`normalized` needs `--reset`. Optional: `python -m app.scripts.inspect_datasets` (scan files), `python -m app.scripts.reset_db`
(empty tables). To approve a proposed hub alias (e.g. Shahbag → Shahbagh) add it to `approved_aliases` in
`dataset_config/hub_aliases.json` and re-run `normalize_datasets`.

Demo logins (all seeded): `admin@dhakapath.local`, `user1@dhakapath.local`, `user2@dhakapath.local`,
`user3@dhakapath.local`, password `password123`. Registered accounts are never verified automatically.

## Run tests
```bash
pytest -q                    # from backend/
pytest backend/app/tests -q  # from the repo root
```
Tests use throwaway SQLite databases and never need an LLM key or network.

## Run the demo script
Start the backend on a seeded database, then, in a second terminal:
```bash
python -m app.scripts.demo_logs --base-url http://localhost:8000 --session DEMO_SESSION --interval 3
```
It calls only real HTTP endpoints (login, search, chat, reports, votes, admin verify, simulation, ride-share, grounding
fake-test, events) and prints each step. Options: `--demo-secret` (default `$DEMO_SECRET`), `--password`.
See `docs/DEMO_SCRIPT.md`. Use a scratch database for rehearsals: `DATABASE_URL=sqlite:///./data/demo.db`.

## Configuration (`.env`)
`DATABASE_URL`, `JWT_SECRET` (use 32+ random bytes), `DEMO_SECRET` (guards `/api/grounding/*` and
`/api/events/publish-debug`), `CORS_ORIGINS`, LLM keys `OPENROUTER_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY`
(all optional: without a key chat uses the rule-based parser). See `.env.example`.

## Frontend integration
- Base URL `http://localhost:8000`; CORS allows `CORS_ORIGINS`.
- Auth: `POST /api/auth/login` → `data.access_token`, send `Authorization: Bearer <token>`.
- Poll `GET /api/events?since_id=<latest_id>&session_id=<id>`; keep the returned `latest_id` as the next cursor.
- Chat: `POST /api/chat` → `data.reply_text`, `data.grounding`, `data.structured_payload`.
- `GET /api/catalog` lists every endpoint and whether it is implemented; unimplemented ones answer HTTP 501 `NOT_IMPLEMENTED`
  (currently the admin route/hub/fare-rule editing endpoints).

## Docs
`docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`, `docs/BACKEND_CONTRACT.md`, `docs/TRANSIT_API.md`, `docs/REPORTS_API.md`,
`docs/SIMULATION.md`, `docs/GROUNDING.md`, `docs/CHAT.md`, `docs/RIDESHARE.md`, `docs/FARE_ENGINE.md`,
`docs/ROUTING_ENGINE.md`, `docs/DATABASE_SCHEMA.md`, `docs/DATASET_CONTRACT.md`.

## Datasets
- Route/co-ordination data: `backend/co-ordination/`
- Fare, metro matrix and rules files: directly under `backend/` (or `backend/datasets/`)
- Raw datasets are never modified.

If `backend/csv` appears: `git mv csv co-ordination` (tracked) or `mv csv co-ordination` (untracked).
