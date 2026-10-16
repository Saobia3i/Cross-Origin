# Demo script (`app/scripts/demo_logs.py`)

A live commute story for judges. The script is an HTTP client only: it never touches the database, so every event,
validation and grounding check you see ran inside the real backend. It prints what the API returned and fabricates nothing.

```bash
python -m app.scripts.seed --source fixtures --reset      # or: normalize_datasets + seed --source normalized --reset
uvicorn app.main:app --reload                              # terminal 1
python -m app.scripts.demo_logs --base-url http://localhost:8000 --session DEMO_SESSION --interval 3   # terminal 2
```
Frontend: poll `GET /api/events?session_id=DEMO_SESSION` while the script runs and the same events appear.

## The story and the endpoints behind it
| Step | What happens | Endpoint(s) | What judges should see |
|---|---|---|---|
| 0 | Log in admin + user1..3 | `POST /api/auth/login` | roles / verified flags |
| A | Public search, student fare, budget 80 | `POST /api/trips/search` | up to 3 route options with fare *ranges*, student fare, grounding badge `grounded` |
| B | Chat in Banglish: "Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao" | `POST /api/chat` | `reply_text` built from engine facts, `grounding.valid=true` |
| C | user1 reports flooding in chat | `POST /api/chat` | a **draft** (not yet a report), `draft_id` |
| D | user1 confirms | `POST /api/reports/drafts/{id}/submit` | report `pending`, `affects_routing=false`, sanity flags |
| E | user2 and user3 vote +1 | `POST /api/reports/{id}/vote` | community score and confidence rise, still `pending` |
| F | Admin verifies | `POST /api/admin/reports/{id}/verify` | `verified`, `affects_routing=true`, extra delay set by the admin |
| G | Search again with the alert live; and avoiding the hub | `POST /api/trips/search` | disruptions / warnings; routes recalculated |
| H | Heavy-rain simulation for this session | `POST /api/simulations` | broken segments, before/after fare and time; a **different session sees nothing** (isolation check) |
| I | Ride-share: user1 posts, user2+user3 join | `POST /api/rideshare/posts`, `/join` | per-person fare **range** and `suggested_safe_per_person_bdt` = ceil(upper bound), labelled "not an official tariff" |
| J | Fake AI route `BUS_MIRPUR_MOTIJHEEL_DIRECT`, fare 25 | `POST /api/grounding/fake-test` (`X-Demo-Secret`) | `blocked=true`, violations `UNKNOWN_ROUTE_ID`, `FARE_MISMATCH`, and the real engine answer instead |
| K | Reset simulation | `POST /api/simulations/reset` | normal fares/routes restored, `active_scenario=None` |
| L | Event poll | `GET /api/events?session_id=DEMO_SESSION` | `report.*`, `simulation.*`, `route.recalculated`, `rideshare.*` in order, `latest_id` cursor |

## Adapting to the data that is seeded
The story prefers **Mirpur 10 → Motijheel** with a flood at **Shahbagh**. If the seeded data lacks a hub, the script
says so (`NOTE: ...`) and uses a really connected pair from `GET /api/transit/routes` (fixtures: Mirpur 10 → Farmgate; the incident
happens at the origin). It never invents a hub, route or fare. With real data, Shahbagh resolves only once the spelling alias is
approved in `dataset_config/hub_aliases.json`.

## Fallbacks
- **No LLM / no keys / quota exhausted:** nothing to do. Chat falls back to the rule-based Banglish/English parser and
  fixed reply templates; replies stay grounded. (`grounding.fallback_used=true` appears only if a rewrite was rejected.)
- **Real datasets missing:** run `seed --source fixtures --reset`. If routes/hubs are empty the script prints
  `Normalized/fixture transit data not available. Run seed --source fixtures for demo.` and exits with code 1 without crashing.
- **No flood-prone data:** step H states that no segments are flagged, so none are broken; fares/times still switch to the rain scenario.
- **Backend down:** prints how to start it, exit code 2. **Wrong `DEMO_SECRET`:** only step J fails (pass `--demo-secret`).
- A step that fails prints the HTTP status and error code, then the script continues; the summary lists each step as `ok` / `FAILED`.
- Re-running: each run adds a new report/post, and a repeated incident can carry a `duplicate` sanity flag. Re-seed with `--reset` for a clean run.
