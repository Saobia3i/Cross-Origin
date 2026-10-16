# DhakaPath — Frontend

React + Vite + TypeScript frontend for the DhakaPath smart transit app (INIF 2026 hackathon).

## Stack
- **React 19** + **Vite** + **TypeScript**
- **MUI v6** — custom City Transit theme (light/dark)
- **React Router** — client-side routing
- **TanStack Query** — server state + polling (alerts every 12s)
- **Zustand** — client state (auth, UI, trip, chat)
- **react-hook-form + zod** — form validation
- **i18next** — EN / বাংলা i18n
- **react-leaflet** — map (teammate's component goes in `features/map/`)
- **MSW** — API mocks for offline development

## Setup

```bash
cd frontend
cp .env.example .env          # set VITE_API_URL to backend server
npm install
npm run dev                   # → http://localhost:5173
```

## Environment

| Variable        | Description                        | Default                  |
|-----------------|-------------------------------------|--------------------------|
| `VITE_API_URL`  | Backend FastAPI URL (teammate's)   | `http://localhost:8000`  |

## Folder structure

```
src/
  app/          i18n, MUI theme, App.tsx (router + providers)
  api/          Axios client, Zod schemas, endpoint functions
  store/        Zustand stores — auth, ui, trip, chat
  components/   StatusBadge, ModePill, FareText, Feedback, Navbar, Layout
  features/
    trip-search/   TripSearchForm, RouteResultsList, RouteDetail
    alerts/        AlertsFeed (+ voting, comments)
    reports/       ReportForm (image upload)
    chat/          ChatDrawer (session, grounding badge)
    simulator/     SimulatorPanel (before/after)
    map/           ← teammate places MapView here
  pages/         HomePage, AlertsPage, SimulatorPage, NetworkPage, AuthPages, AdminPage
  styles/        tokens.css, global.css
```

## Map integration

The map is a self-contained component. Teammate places `MapView.tsx` in `src/features/map/`.

**Props in:** `hubs`, `routes`, `alerts`, `selectedRoute`, `alternativeRoute`, `closedRouteIds`, `highlightHubIds`, `theme`, `layers`

**Events out:** `onHubClick(hubId)`, `onAlertClick(alertId)`, `onMapClick(lat, lng)`, `onRouteClick(routeId)`

In `HomePage.tsx`, replace `<MapPlaceholder />` with `<MapView ... />`.

## Routes

| Path         | Page            |
|--------------|-----------------|
| `/`          | Home (map + search + alerts) |
| `/alerts`    | Alerts feed     |
| `/simulator` | Scenario simulator |
| `/network`   | Hub/route browser |
| `/report`    | Report form     |
| `/login`     | Login           |
| `/register`  | Register        |
| `/admin`     | Admin dashboard |
