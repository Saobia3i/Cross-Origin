# Merge Notes

- Backend code lives only inside `backend/`.
- Raw datasets are never modified (read-only loaders):
  - `backend/Dhaka_*.csv`
  - `backend/datasets/**`
  - `backend/co-ordination/**`
- Generated normalized data goes to `backend/data/generated/`. It is git-ignored except `.gitkeep`;
  regenerate it with `python -m app.scripts.inspect_datasets && python -m app.scripts.normalize_datasets`.
- Uploads go to `backend/data/uploads/` (git-ignored except `.gitkeep`).
- The SQLite DB path is configurable via `DATABASE_URL` (default `backend/data/dhakapath.db`).
- Seed scripts (in `app/scripts/`) must be idempotent. The normalize script is: it rewrites its outputs
  deterministically.
- The API catalog is in `backend/app/api_catalog.py`. The frontend should use `GET /api/catalog` to
  discover endpoints.
- The official dataset folder is `backend/co-ordination`. If `backend/csv` appears again, rename it:
  `git mv backend/csv backend/co-ordination` if tracked, otherwise a plain move. Inspection warns when
  `backend/csv` exists.
- Dataset interpretation lives in `backend/dataset_config/*.json`, not in code. Edit those files when a
  dataset changes:
  - `coordination_mapping.json`: co-ordination layer → mode/role and trailing fields
  - `hub_aliases.json`: approved/rejected aliases
  - `mode_aliases.json`
  - `problem_owner_rules.json`: verbatim brief
- Test fixtures live in `backend/tests/fixtures/mini_datasets/`. They are synthetic, never used in
  production, and processed only with `--source fixtures`, which writes to `data/generated/fixtures/`.
- The database file `backend/data/dhakapath.db` is git-ignored. Teammates build their own with
  `python -m app.scripts.seed --source normalized`. Fixture data is never mixed into a production DB.
