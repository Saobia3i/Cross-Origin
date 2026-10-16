# DhakaPath Backend

FastAPI + SQLModel + SQLite. Python 3.11+.

## Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

## Run
```bash
uvicorn app.main:app --reload
```
Try `http://localhost:8000/health` and `http://localhost:8000/api/catalog` (docs at `/docs`).

## Datasets → normalized JSON
```bash
python -m app.scripts.inspect_datasets     # scan files, write data/generated/dataset_manifest.json
python -m app.scripts.normalize_datasets   # write data/generated/normalized_*.json + normalization_report.json
```
Add `--source fixtures` to either command to run on the synthetic test fixtures; output goes to
`data/generated/fixtures/`. Read `data/generated/normalization_report.json` for warnings, proposed hub
aliases, label conflicts and hubs without coordinates.

To approve a proposed alias (e.g. Shahbag → Shahbagh), add it to `approved_aliases` in
`dataset_config/hub_aliases.json` and re-run `normalize_datasets`.

## Database
```bash
python -m app.scripts.seed --source normalized          # users + dataset tables from data/generated/normalized_*.json
python -m app.scripts.seed --source fixtures --reset    # synthetic test fixtures only
python -m app.scripts.reset_db                          # empty tables
```
Seeding is idempotent. See `docs/DATABASE_SCHEMA.md`. Demo logins: `admin@dhakapath.local`,
`user1..3@dhakapath.local`, password `password123`.

## Tests
```bash
pytest                       # from backend/
pytest backend/app/tests -q  # from the repo root
```

## Reset DB
`python -m app.scripts.reset_db`, or `seed --reset`. Stop the server first so the file is not locked.

## Datasets
- Route/co-ordination data: `backend/co-ordination/`
- Fare, metro matrix and rules files: directly under `backend/` (or `backend/datasets/`)
- See `docs/DATASET_CONTRACT.md` and `docs/DATASET_SCHEMA_MAP.md`.

If `backend/csv` appears: `git mv csv co-ordination` (tracked) or `mv csv co-ordination` (untracked).
