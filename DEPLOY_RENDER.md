# 🚀 Render Deployment Guide — DhakaPath (Cross-Origin)

> Deploys **2 services + 1 database** on Render's free tier using the `render.yaml` Blueprint.

---

## What Gets Deployed

| Service | Type | URL (after deploy) |
|---|---|---|
| `dhakapath-backend` | Web Service (Python/FastAPI) | `https://dhakapath-backend.onrender.com` |
| `dhakapath-frontend` | Static Site (React/Vite) | `https://dhakapath-frontend.onrender.com` |
| `dhakapath-db` | PostgreSQL (free) | Internal connection string |

> ⚠️ **Render free tier** spins down after 15 min of inactivity. The first request after sleep can take ~30 seconds.

---

## Step-by-Step Deploy

### 1. Push to GitHub
Make sure your repo is pushed to GitHub (or GitLab). The `render.yaml` file must be in the **repo root**.

```bash
git add render.yaml backend/render_start.sh backend/requirements.txt backend/app/main.py
git commit -m "chore: add Render deployment config"
git push
```

### 2. Create a New Blueprint on Render

1. Go to **https://dashboard.render.com**
2. Click **New +** → **Blueprint**
3. Connect your GitHub account and select the **Cross-Origin** repo
4. Render will auto-detect `render.yaml` — click **Apply**

### 3. Set Secret Environment Variables

After clicking Apply, Render will show a setup screen for `sync: false` variables.  
Fill in at least one LLM key:

| Variable | Where to get it |
|---|---|
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey |
| `GROQ_API_KEY` | https://console.groq.com/keys |

> Leave unused ones blank — the backend falls back to rule-based parsing without a key.

### 4. Fix the CORS Origin (after backend is live)

Once the backend deploys, Render assigns it a URL like:
`https://dhakapath-backend.onrender.com`

Update the **backend service** env var in the Render dashboard:
```
CORS_ORIGINS = https://dhakapath-frontend.onrender.com
```

And update the **frontend service** env var:
```
VITE_API_BASE_URL = https://dhakapath-backend.onrender.com
```

Then trigger a **Manual Deploy** on the frontend so Vite rebuilds with the new URL.

### 5. Seed the Database (first time only)

The `render_start.sh` script runs the seeder on every deploy — it is **idempotent** so it won't duplicate data.  
On the very first deploy it will seed:
- Demo users (`admin@dhakapath.local` etc. with password `password123`)
- Default fare rules
- Transit network (if `data/generated/normalized_*.json` is present in the repo)

---

## Files Created / Changed for Deployment

| File | Action | Purpose |
|---|---|---|
| [`render.yaml`](file:///e:/vs%20code%20projects/IAIT%20hackathon/cross-origin/Cross-Origin/render.yaml) | **Created** | Blueprint defining all 3 Render services |
| [`backend/render_start.sh`](file:///e:/vs%20code%20projects/IAIT%20hackathon/cross-origin/Cross-Origin/backend/render_start.sh) | **Created** | Startup script: seed DB → start uvicorn |
| [`backend/requirements.txt`](file:///e:/vs%20code%20projects/IAIT%20hackathon/cross-origin/Cross-Origin/backend/requirements.txt) | **Modified** | Added `psycopg2-binary` for PostgreSQL |
| [`backend/app/main.py`](file:///e:/vs%20code%20projects/IAIT%20hackathon/cross-origin/Cross-Origin/backend/app/main.py) | **Modified** | Added `/health` endpoint for Render health checks |

---

## Architecture on Render

```
Browser
  │
  ▼
┌─────────────────────────────────┐
│  dhakapath-frontend             │  Static Site (CDN)
│  React/Vite SPA                 │
│  VITE_API_BASE_URL → backend    │
└────────────────┬────────────────┘
                 │  HTTPS API calls
                 ▼
┌─────────────────────────────────┐
│  dhakapath-backend              │  Web Service (Python 3)
│  FastAPI + uvicorn              │
│  PORT injected by Render        │
│  CORS: allows frontend origin   │
└────────────────┬────────────────┘
                 │  DATABASE_URL (auto-linked)
                 ▼
┌─────────────────────────────────┐
│  dhakapath-db                   │  PostgreSQL (free tier)
│  Auto-provisioned by Render     │
└─────────────────────────────────┘
```

---

## Important Notes

### ⚠️ Free Tier Limitations
- **PostgreSQL free tier expires after 90 days** — you'll need to upgrade or re-create it
- Backend and frontend free services **spin down** after 15 min of inactivity
- Uploaded images in `/tmp` are **ephemeral** — they will be lost on restart (upgrade to paid + Render Disk for persistence)

### 🔑 Default Login (after seed)
| Email | Password | Role |
|---|---|---|
| `admin@dhakapath.local` | `password123` | Admin |
| `user1@dhakapath.local` | `password123` | User |

### 🔁 Re-deploying
Every `git push` to your connected branch triggers an auto-deploy.  
The seed script is idempotent — data won't be duplicated.

### 🐞 Checking Logs
In the Render dashboard → select a service → **Logs** tab.  
The backend prints startup output including seeder results.
