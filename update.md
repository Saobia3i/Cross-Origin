# 🔄 Update Log — Cross-Origin / DhakaPath

> **Date:** 2026-10-16  

## Bonus Features — Community Tools

### Signboard & Notice Digitization
- New **Community** page lets users upload a JPG, PNG, or WebP photo of a bus signboard, fare chart, or handwritten route note.
- `POST /api/digitize/signboard` sends the image to configured Gemini Vision and returns a reviewable structured draft: route name, operator, mode, origin/destination, stops, fare rows, OCR text, and confidence score.
- The result is intentionally a review draft: community signage does not silently overwrite verified transit data.
- Configure `GEMINI_API_KEY` in `backend/.env` to enable live image extraction. Upload size is limited to 8 MB.

### Commuter Ride-Share & Fare Bot
- Added the **Hub ride-share board** under `/community`, backed by the existing rideshare API.
- Users can filter rides by hub, post CNG/rickshaw rides, join or leave open rides, and see remaining seats.
- The fare bot displays the equal per-person estimated split and recalculates automatically whenever participants join or leave.
- Posting, joining, and leaving require a verified account; browsing remains available to guests.
> **Session:** Local dev environment setup & CORS fix

---

## ✅ Changes Made

### 1. Backend — Port Changed (8000 → 8080)
**Reason:** OS-level socket permission error blocked port `8000`.

```
WinError 10013: An attempt was made to access a socket in a way
forbidden by its access permissions
```

**Fix:** Backend uvicorn server now runs on **port 8080** instead of the default 8000.

```powershell
# Command used to start backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

---

### 2. New File Created — `frontend/.env`
**File:** `e:\vs code projects\IAIT hackathon\cross-origin\Cross-Origin\frontend\.env`

**Reason:** No `.env` existed in the frontend. Without it, `VITE_API_BASE_URL` defaults to `http://localhost:8000`, which was the wrong port.

**Contents:**
```env
VITE_API_BASE_URL=http://localhost:8080
VITE_USE_MOCKS=false
VITE_EVENT_POLL_INTERVAL_MS=12000
```

---

### 3. Backend `.env` Modified — CORS Origins Updated
**File:** `e:\vs code projects\IAIT hackathon\cross-origin\Cross-Origin\backend\.env`

**Reason:** The Vite dev server started on port `5174` (since `5173` was already in use). The backend's `CORS_ORIGINS` only allowed `5173` and `3000`, causing CORS errors in the browser for all API calls.

**Change (diff):**
```diff
- CORS_ORIGINS=http://localhost:5173,http://localhost:3000
+ CORS_ORIGINS=http://localhost:5173,http://localhost:5174,http://localhost:3000
```

---

### 4. Backend Restarted
After updating `backend/.env`, the backend was restarted so the new CORS config would take effect (uvicorn `--reload` only watches `.py` files, not `.env`).

---

## 🚀 Running Services (after all changes)

| Service       | URL                           |
|---------------|-------------------------------|
| **Frontend**  | http://localhost:5174/        |
| **Backend**   | http://localhost:8080/        |
| **API Docs**  | http://localhost:8080/docs    |

---

## 🔑 Default Login Credentials

All accounts use password: **`password123`**

| Email                        | Role      |
|------------------------------|-----------|
| `admin@dhakapath.local`      | **Admin** |
| `user1@dhakapath.local`      | User      |
| `user2@dhakapath.local`      | User      |
| `user3@dhakapath.local`      | User      |

> ⚠️ If login fails, the database may not be seeded yet. Run:
> ```powershell
> .\.venv\Scripts\python.exe -m app.scripts.seed --source fixtures --reset
> ```
> Then restart the backend.

---

## 📁 Files Changed Summary

| File | Action | Reason |
|------|--------|--------|
| `frontend/.env` | **Created (new)** | Point frontend API to port 8080 |
| `backend/.env` | **Modified** | Add port 5174 to CORS_ORIGINS |

---

## 📝 Notes

- Ports `8000` and `5173` were **unavailable** on this machine (OS permission / already in use).
- The `frontend/.env` file is in `.gitignore` — it will **not be committed** to git. Each developer needs to create their own.
- The `backend/.env` **is tracked** — the CORS change will apply for all team members who pull the update.
