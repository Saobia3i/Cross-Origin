# Frontend on Vercel (backend stays on Render)

Backend: https://cross-origin-xd5y.onrender.com

## 1. Import the project
Vercel -> Add New -> Project -> import `Saobia3i/Cross-Origin`.

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Vite (auto) |
| Build / Output | `npm run build` / `dist` (from `frontend/vercel.json`) |

## 2. Environment variables (Vercel -> Settings -> Environment Variables)
| Key | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://cross-origin-xd5y.onrender.com` (no trailing `/`) |
| `VITE_USE_MOCKS` | `false` |
| `VITE_EVENT_POLL_INTERVAL_MS` | `12000` |

Vite bakes these in at build time, so redeploy after changing them.

## 3. Allow the Vercel URL on the backend (Render -> backend -> Environment)
| Key | Value |
|---|---|
| `CORS_ORIGINS` | `https://<your-app>.vercel.app` (comma-separate several) |
| `CORS_ORIGIN_REGEX` | optional, to allow preview deploys: `https://.*\.vercel\.app` |

Without this the browser blocks every API call (CORS).

## 4. Check
Open `https://<your-app>.vercel.app`, log in with `admin@dhakapath.local` / `password123`.
The first request can take ~30-60 s if the free Render backend was asleep.
