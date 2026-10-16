Below is the \*\*backend-only plan\*\* for DhakaPath, corrected against the gaps you listed and optimized so you can build it fast in \~1 hour with LLM assistance.



I will keep it practical: what backend owns, how it behaves, exact folder structure, API file, database, routing algorithm adaptation, demo log script, and build order.



\---



\# 1. Backend Scope: What Backend Must Handle



Backend is the \*\*single source of truth\*\*. Frontend only displays what backend returns.



\## Backend owns these responsibilities



\### A. Auth and roles



Backend handles:



\- Login

\- JWT or session token

\- Roles:

&#x20; - `guest`: no user record, can browse routes/search/map/public alerts

&#x20; - `user`: verified logged-in user, can submit reports, confirm/upvote/downvote, request resolve

&#x20; - `admin`: can verify/reject/reopen reports, block/unblock roads, edit fares, control simulation if needed



Important rule:



\- Reports can be created only by authenticated verified users.

\- Route search is public.

\- Fare data comes from DB.

\- Admin approval/rejection is final for report status.

\- User votes affect confidence/community score, not final approval.



\---



\### B. Transit network CMS



Backend stores and serves:



\- Hubs

\- Routes/edges

\- Fare rules

\- Connector fare ranges

\- Boarding/alighting notes

\- Mode list including `walk`



Modes:



```text

bus

mrt

leguna

rickshaw

cng

walk

```



Hub status is derived, not manually stored unless needed.



Derived hub status:



```text

normal

delayed

blocked

```



Backend computes hub status from active verified alerts and closed route edges.



\---



\### C. Street alerts / reports



Backend handles report lifecycle:



```text

draft → pending → verified → resolved

&#x20;                ↘ rejected

&#x20;                ↘ expired

```



Canonical status list:



```text

draft

pending

verified

rejected

resolved

expired

```



Rules:



\- Chat report creation first creates `draft`.

\- User confirms draft, then it becomes `pending`.

\- Logged users can upvote/downvote/confirm.

\- Admin verifies or rejects.

\- Users can mark resolved, admin can reopen.

\- Only `verified`, non-expired, confidence-above-threshold alerts affect routing.

\- `pending`, `rejected`, `resolved`, `expired` do not affect routing.



Reports must include:



```text

report\_type

severity

affected\_hub

affected\_route\_ids

description

incident\_time

reported\_by

created\_at

last\_verified\_at

upvotes

downvotes

confidence

freshness

half\_life\_min

expires\_at

status

edge\_closed

extra\_time\_min

```



Report types:



```text

waterlogging

road\_construction

severe\_gridlock

intersection\_blockade

fare\_surge

accident

all\_clear

other

```



For fare reports:



```text

report\_type = fare\_surge

mode = rickshaw/cng/bus/etc

fare\_amount\_bdt = submitted fare

route\_id optional

affected\_hub required

```



\---



\### D. Route search



Backend handles:



\- Natural language trip search

\- Structured trip search

\- Multi-leg route generation

\- Budget constraint

\- Student fare preference

\- Avoid hubs

\- Avoid modes

\- Disruption-aware routing

\- Session-based simulation routing

\- Return at least 3 route options for normal unconstrained hub pairs



Endpoint:



```http

POST /api/trips/search

```



It should accept either structured input:



```json

{

&#x20; "origin": "Mirpur 10",

&#x20; "destination": "Motijheel",

&#x20; "budget\_bdt": 80,

&#x20; "prefer\_student\_fare": true,

&#x20; "avoid\_hubs": \["Shahbagh"],

&#x20; "optimize": "balanced",

&#x20; "session\_id": "DEMO\_SESSION"

}

```



or natural language:



```json

{

&#x20; "query\_text": "Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao",

&#x20; "session\_id": "DEMO\_SESSION"

}

```



If `query\_text` exists, backend parses intent/entities first, then calls route engine.



\---



\### E. Fare engine



Backend calculates all fares from DB.



Rules:



\- Bus student fare:



```text

student\_fare = max(10, ceil(base\_fare \* 0.5))

```



Use integer BDT to avoid weird totals like `62.5`.



\- MRT:

&#x20; - configurable student eligibility

&#x20; - in demo, assume no student discount unless DB says otherwise



\- Rickshaw/CNG:

&#x20; - no student discount by default

&#x20; - fare is range, not single value

&#x20; - baseline/rush/rain ranges from DB



\- Walk:

&#x20; - fare 0

&#x20; - no student rule



Total fare must support ranges:



```json

{

&#x20; "total\_regular\_fare\_min\_bdt": 70,

&#x20; "total\_regular\_fare\_max\_bdt": 70,

&#x20; "total\_student\_fare\_min\_bdt": 55,

&#x20; "total\_student\_fare\_max\_bdt": 55

}

```



If route contains rickshaw/CNG:



```json

{

&#x20; "total\_regular\_fare\_min\_bdt": 90,

&#x20; "total\_regular\_fare\_max\_bdt": 120,

&#x20; "total\_student\_fare\_min\_bdt": 90,

&#x20; "total\_student\_fare\_max\_bdt": 120

}

```



If no student discount applies:



```text

student\_min = regular\_min

student\_max = regular\_max

```



\---



\### F. Duration engine



Total duration must add up correctly.



```text

total\_duration\_min =

sum(leg\_travel\_time\_min)

\+ sum(transfer\_wait\_min)

\+ sum(walking\_time\_min)

```



Walking can be:



\- a leg with mode `walk`

\- or transfer wait, depending on route design



Preferred: make walking an actual leg when needed.



Transfer wait examples:



```text

bus → mrt: 7 min

mrt → bus: 7 min

bus → bus different route: 5 min

rickshaw → any: 3 min

any → rickshaw: 3 min

walk leg: 0 extra wait

```



Store transfer wait in route response:



```json

{

&#x20; "total\_travel\_time\_min": 40,

&#x20; "total\_transfer\_wait\_min": 10,

&#x20; "total\_walking\_time\_min": 5,

&#x20; "total\_duration\_min": 55

}

```



\---



\### G. Disruption simulation



Simulation must be \*\*per session\*\*, not global.



Why:



\- Judge A can run rain demo without breaking Judge B’s view.

\- Frontend can pass `session\_id`.

\- Backend stores active scenario in SQLite.



Scenario types:



```text

heavy\_rain

peak\_rush\_hour

blocked\_intersection

```



Effects:



\#### Heavy rain



\- Apply rain fare range to rickshaw/CNG

\- Add time multiplier

\- Close flood-prone edges in selected zone/hub

\- Optionally create simulated waterlogging alert



\#### Peak rush hour



\- Apply rush fare range to rickshaw/CNG

\- Add time multiplier

\- Increase transfer wait



\#### Blocked intersection



\- Close all edges touching selected hub, except maybe walking access

\- Force detour



Backend response must include:



```text

broken\_segments

detour\_routes

before\_after\_comparison

```



\---



\### H. Report validation, decay, sanity checks



Backend must validate reports before they affect routing.



Sanity checks:



\- Incident time not future

\- Incident time not older than 24 hours

\- Fare amount realistic for mode

\- Duplicate report within 10 minutes

\- Contradictory report held for admin review

\- Missing hub/route rejected

\- Unauthenticated report rejected



Decay:



Use `last\_verified\_at`, `half\_life\_min`, `expires\_at`, `confidence`, `freshness`.



Suggested formula:



```text

age\_min = now - last\_verified\_at

decay\_factor = 0.5 ^ (age\_min / half\_life\_min)



base\_confidence = severity\_base + vote\_score + admin\_bonus

confidence = clamp(base\_confidence \* decay\_factor, 0, 1)



freshness = decay\_factor

expires\_at = last\_verified\_at + half\_life\_min \* 2

```



Vote score:



```text

vote\_score = 0.05 \* upvotes - 0.08 \* downvotes

```



Admin bonus:



```text

if status == verified: +0.20

if status == rejected: confidence = 0

if status == resolved: confidence = 0 for routing

```



Routing impact threshold:



```text

status == verified

AND confidence >= 0.40

AND now < expires\_at

```



Half-life examples:



```text

waterlogging: 60 min

severe\_gridlock: 30 min

intersection\_blockade: 30 min

road\_construction: 24 hours

fare\_surge: 45 min

```



\---



\### I. Grounding validator



Backend must prevent AI hallucination.



Rule:



\- LLM may parse intent.

\- LLM may rewrite explanation.

\- LLM must not invent routes, fares, hubs, alerts.



Grounding checker validates:



\- every `hub\_id` exists

\- every `route\_id` exists

\- every fare matches DB/engine range

\- every duration matches engine calculation

\- every alert referenced exists

\- student fare rule is correct



If AI output fails grounding:



\- discard AI route

\- use deterministic engine result

\- return grounding badge as failed/fallback used



Add judge demo endpoint:



```http

POST /api/grounding/fake-test

```



It intentionally submits a fake AI route and shows backend blocking it.



\---



\### J. Chat assistant backend



All chat prompts go to backend first.



Flow:



```text

Frontend chat input

→ POST /api/chat

→ Backend loads session context

→ Backend calls LLM only for intent/entity extraction

→ Backend resolves hubs using alias/fuzzy match

→ Backend calls route/fare/alert/simulation engines

→ Backend grounds result

→ Backend returns structured response + reply\_text

→ Frontend renders cards/map/buttons

```



Important:



Use field:



```json

"reply\_text": "..."

```



Do not use:



```json

"reply": { "text": "..." }

```



Chat must support:



\- find route

\- compare fare

\- submit disruption report as draft

\- confirm draft report

\- request alternative route

\- ask fare rule

\- ask alert reliability

\- maintain context



Context state example:



```json

{

&#x20; "last\_origin": "MIRPUR\_10",

&#x20; "last\_destination": "MOTIJHEEL",

&#x20; "last\_route\_id": "OPT\_CHEAPEST\_001",

&#x20; "last\_budget\_bdt": 80,

&#x20; "last\_prefer\_student\_fare": true,

&#x20; "last\_avoid\_hubs": \["SHAHBAGH"],

&#x20; "last\_report\_draft\_id": "DRAFT\_001",

&#x20; "language": "banglish"

}

```



\---



\### K. Live demo event feed



Backend must support dummy real-time demo logs.



Use an `events` table.



Every important action writes an event:



```text

report.created

report.draft.created

report.draft.submitted

report.confirmed

report.downvoted

report.verified

report.rejected

report.resolved

report.expired

simulation.applied

simulation.reset

route.recalculated

grounding.blocked

chat.message

```



Frontend polls:



```http

GET /api/events?since\_id=123\&session\_id=DEMO\_SESSION

```



Python demo script calls real backend endpoints, backend stores events, frontend displays them as live activity.



\---



\# 2. Corrected Backend Rules From Your Gap List



Use these exact decisions.



\## Part 1 fixes



\### Hub status



Add derived hub status:



```json

{

&#x20; "hub\_id": "SHAHBAGH",

&#x20; "name": "Shahbagh",

&#x20; "status": "blocked",

&#x20; "status\_reason": "Verified intersection blockade",

&#x20; "active\_alert\_count": 1

}

```



\### Walk mode



Include:



```text

walk

```



Walking edges:



```json

{

&#x20; "route\_id": "WALK\_FARMGATE\_MRT\_ENTRANCE",

&#x20; "mode": "walk",

&#x20; "origin\_hub": "FARMGATE",

&#x20; "destination\_hub": "FARMGATE\_MRT",

&#x20; "fare\_min\_bdt": 0,

&#x20; "fare\_max\_bdt": 0,

&#x20; "estimated\_time\_min": 4

}

```



\---



\## Part 2 fixes



\### Natural language search



`POST /api/trips/search` must accept:



```json

{

&#x20; "query\_text": "Mirpur theke Motijheel, budget 60 taka, student fare"

}

```



Backend parses:



```json

{

&#x20; "intent": "find\_route",

&#x20; "origin": "MIRPUR\_10",

&#x20; "destination": "MOTIJHEEL",

&#x20; "budget\_bdt": 60,

&#x20; "prefer\_student\_fare": true

}

```



Then calls route engine.



\---



\## Part 3 fixes



\### All simulation types



Support:



```text

heavy\_rain

peak\_rush\_hour

blocked\_intersection

```



Example request:



```json

{

&#x20; "session\_id": "DEMO\_SESSION",

&#x20; "scenario\_type": "peak\_rush\_hour",

&#x20; "zone\_hub": "FARMGATE"

}

```



```json

{

&#x20; "session\_id": "DEMO\_SESSION",

&#x20; "scenario\_type": "blocked\_intersection",

&#x20; "zone\_hub": "SHAHBAGH"

}

```



\### Fare totals must be ranges



Do not return single `total\_fare\_bdt` if connector range exists.



Return:



```json

{

&#x20; "total\_regular\_fare\_min\_bdt": 90,

&#x20; "total\_regular\_fare\_max\_bdt": 120,

&#x20; "total\_student\_fare\_min\_bdt": 90,

&#x20; "total\_student\_fare\_max\_bdt": 120

}

```



\### Student total correctness



If no leg has student discount:



```text

student\_min = regular\_min

student\_max = regular\_max

```



Example:



Rickshaw + MRT, no student discount:



```json

{

&#x20; "total\_regular\_fare\_min\_bdt": 90,

&#x20; "total\_regular\_fare\_max\_bdt": 120,

&#x20; "total\_student\_fare\_min\_bdt": 90,

&#x20; "total\_student\_fare\_max\_bdt": 120

}

```



\### Duration correctness



Return:



```json

{

&#x20; "total\_travel\_time\_min": 40,

&#x20; "total\_transfer\_wait\_min": 10,

&#x20; "total\_walking\_time\_min": 5,

&#x20; "total\_duration\_min": 55

}

```



Leg times must sum correctly.



\---



\## Part 4 fixes



\### Status names



Use only:



```text

draft

pending

verified

rejected

resolved

expired

```



Do not mix:



```text

active

flagged

community\_verified

```



unless as separate boolean fields.



Recommended extra fields:



```json

{

&#x20; "status": "pending",

&#x20; "admin\_approved": false,

&#x20; "community\_score": 2,

&#x20; "affects\_routing": false

}

```



\### Confirm and resolve endpoints



Add:



```http

POST /api/reports/{report\_id}/confirm

POST /api/reports/{report\_id}/resolve

POST /api/reports/{report\_id}/vote

```



Admin:



```http

POST /api/admin/reports/{report\_id}/verify

POST /api/admin/reports/{report\_id}/reject

POST /api/admin/reports/{report\_id}/reopen

```



\### Decay visibility



Report response must include:



```json

{

&#x20; "confidence": 0.74,

&#x20; "freshness": 0.82,

&#x20; "half\_life\_min": 60,

&#x20; "last\_verified\_at": "2026-10-16T10:10:00Z",

&#x20; "expires\_at": "2026-10-16T11:10:00Z",

&#x20; "affects\_routing": true

}

```



\### Fare report type



Add:



```text

fare\_surge

```



Fields:



```json

{

&#x20; "report\_type": "fare\_surge",

&#x20; "mode": "rickshaw",

&#x20; "fare\_amount\_bdt": 180,

&#x20; "affected\_hub": "GULSHAN"

}

```



Sanity check flags unrealistic fare.



\---



\## Part 5 fixes



\### Alert reliability response



Add structured payload:



```json

{

&#x20; "reliability": {

&#x20;   "report\_id": "REPORT\_SHAHBAGH\_001",

&#x20;   "status": "verified",

&#x20;   "confidence": 0.86,

&#x20;   "freshness": 0.91,

&#x20;   "upvotes": 5,

&#x20;   "downvotes": 0,

&#x20;   "last\_verified\_at": "2026-10-16T10:10:00Z",

&#x20;   "expires\_at": "2026-10-16T11:10:00Z",

&#x20;   "admin\_approved": true,

&#x20;   "affects\_routing": true,

&#x20;   "reasons": \[

&#x20;     "Admin verified",

&#x20;     "Multiple recent confirmations",

&#x20;     "No contradictory report"

&#x20;   ]

&#x20; }

}

```



\### Chat report flow



Wrong:



```text

chat message → report pending immediately

```



Correct:



```text

chat message → draft created → user confirms → pending → admin verifies

```



Response after chat report intent:



```json

{

&#x20; "intent": "submit\_disruption\_report",

&#x20; "reply\_text": "Ami report draft taiyaar korechi. Confirm korle pending queue e jabe.",

&#x20; "structured\_payload": {

&#x20;   "draft\_report": {

&#x20;     "draft\_id": "DRAFT\_001",

&#x20;     "report\_type": "waterlogging",

&#x20;     "severity": "heavy",

&#x20;     "affected\_hub": "SHAHBAGH",

&#x20;     "status": "draft"

&#x20;   },

&#x20;   "confirmation\_required": true

&#x20; }

}

```



Then:



```http

POST /api/reports/drafts/DRAFT\_001/submit

```



\---



\# 3. Recommended Tech Stack



Use this for speed.



\## Backend



```text

Python 3.11+

FastAPI

Uvicorn

SQLModel or SQLAlchemy 2.0

SQLite

Pydantic

python-jose or PyJWT

passlib\[bcrypt]

httpx

pytest

python-dotenv

```



Recommended choice:



```text

FastAPI + SQLModel + SQLite + Pydantic

```



Why:



\- Fast to generate with LLM

\- Less boilerplate than Django

\- SQLite easy for hackathon

\- Pydantic validates AI/tool output

\- FastAPI gives OpenAPI automatically



\---



\## Routing/pathfinding



Use your A\* idea, but modify it.



Do not use raw Euclidean distance on latitude/longitude.



Use:



```text

Haversine distance

```



For cheapest fare search:



```text

Dijkstra, heuristic = 0

```



For fastest time search:



```text

A\*, heuristic = haversine\_distance / max\_average\_speed

```



For balanced/fewest transfers:



```text

Dijkstra or A\* with conservative heuristic = 0

```



Because graph is small, correctness matters more than micro-optimization.



\---



\## LLM



Primary:



```text

OpenRouter free model

```



Fallback:



```text

Gemini free tier

Groq free tier

```



Last fallback:



```text

rule-based keyword parser

```



Backend must not depend on LLM for route correctness.



\---



\## Demo event feed



Use simple polling first.



```text

GET /api/events?since\_id=...

```



Optional later:



```text

Server-Sent Events

WebSocket

```



For 1-hour build, polling is safer.



\---



\# 4. Pathfinding Algorithm: How To Adapt Your A\* Code



Your code is a good base, but backend needs multi-modal constrained search.



\## Changes required



\### 1. Use Haversine



Replace:



```python

math.sqrt((x1 - x2)\*\*2 + (y1 - y2)\*\*2)

```



with:



```python

def haversine\_min(lat1, lon1, lat2, lon2):

&#x20;   # returns approximate distance in km

```



Then heuristic for time:



```python

distance\_km / max\_speed\_kmh \* 60

```



Use max speed conservatively, e.g.:



```text

40 km/h

```



So heuristic does not overestimate.



\---



\### 2. Edge is not just road



Each edge has:



```text

route\_id

mode

origin\_hub

destination\_hub

fare\_min

fare\_max

travel\_time\_min

student\_eligible

status

flood\_prone

tags

```



\---



\### 3. Search objective



Support:



```text

cheapest

fastest

fewest\_transfers

balanced

```



Weight examples:



\#### Cheapest



```text

edge\_cost = fare\_min

```



\#### Fastest



```text

edge\_cost = travel\_time\_min + delay\_min

```



\#### Fewest transfers



```text

edge\_cost = transfer\_penalty \* 1000 + travel\_time\_min

```



\#### Balanced



```text

edge\_cost =

&#x20; 0.4 \* normalized\_fare

\+ 0.4 \* normalized\_time

\+ 0.2 \* transfer\_penalty

\+ disruption\_risk\_penalty

```



For hackathon simplicity, run separate searches:



```text

cheapest\_search()

fastest\_search()

fewest\_transfers\_search()

balanced\_search()

```



Then deduplicate by route edge sequence.



If fewer than 3 unique routes, rerun with penalized used edges.



\---



\### 4. Constraints



Before search, filter edges:



```text

exclude closed route.status

exclude session closed edges

exclude verified alert closed edges

exclude avoided hubs

exclude avoided modes

respect budget after fare calculation

```



Budget check:



```text

if route.total\_regular\_fare\_min\_bdt > budget:

&#x20;   mark within\_budget = false

&#x20;   add warning

```



If strict budget required:



```text

discard route

```



Recommended:



\- return route but warn if max exceeds budget

\- discard only if min exceeds budget



\---



\### 5. Transfer calculation



Count transfer when vehicle changes.



Example:



```text

bus → mrt = 1 transfer

mrt → rickshaw = 1 transfer

bus → bus different route = 1 transfer

walk leg = may not count as vehicle transfer, but adds walking time

```



Simple rule:



```text

transfers = number of mode-changing boundaries

```



or:



```text

transfers = len(legs) - 1

```



Choose one and be consistent.



Recommended:



```text

transfers = len(vehicle\_legs) - 1

```



where `vehicle\_legs` excludes pure walking access legs.



But for demo simplicity, you can use:



```text

transfers = len(legs) - 1

```



and document it.



\---



\### 6. Return at least 3 options



Backend function:



```python

def search\_routes(origin, destination, constraints, session\_id):

&#x20;   candidates = \[]



&#x20;   for objective in \["cheapest", "fastest", "fewest\_transfers", "balanced"]:

&#x20;       result = planner.search(objective)

&#x20;       add\_unique(candidates, result)



&#x20;   while len(unique\_candidates) < 3 and attempts < 10:

&#x20;       penalize previously used edges

&#x20;       rerun cheapest/fastest

&#x20;       add\_unique(candidates, result)



&#x20;   compute fares and durations

&#x20;   rank

&#x20;   return top routes

```



Test all hub pairs with a script.



\---



\# 5. Backend Folder Structure



Keep it small but clean.



```text

backend/

&#x20; app/

&#x20;   \_\_init\_\_.py

&#x20;   main.py

&#x20;   config.py

&#x20;   db.py

&#x20;   models.py

&#x20;   schemas.py

&#x20;   security.py

&#x20;   deps.py

&#x20;   api.py

&#x20;   api\_catalog.py



&#x20;   services/

&#x20;     \_\_init\_\_.py

&#x20;     auth\_service.py

&#x20;     transit\_service.py

&#x20;     routing\_service.py

&#x20;     fare\_service.py

&#x20;     alert\_service.py

&#x20;     simulation\_service.py

&#x20;     grounding\_service.py

&#x20;     chat\_service.py

&#x20;     event\_service.py

&#x20;     nlp\_service.py

&#x20;     llm\_service.py



&#x20;   scripts/

&#x20;     \_\_init\_\_.py

&#x20;     seed.py

&#x20;     demo\_logs.py

&#x20;     test\_all\_pairs.py



&#x20;   tests/

&#x20;     \_\_init\_\_.py

&#x20;     test\_fares.py

&#x20;     test\_routing.py

&#x20;     test\_alerts.py

&#x20;     test\_grounding.py

&#x20;     test\_chat.py



&#x20; data/

&#x20;   dhakapath.db

&#x20;   uploads/



&#x20; .env

&#x20; .env.example

&#x20; requirements.txt

&#x20; README.md

```



If you want even faster, merge some services, but this structure is good for LLM generation.



\---



\# 6. Important Files



\## `app/main.py`



Responsibilities:



\- create FastAPI app

\- CORS

\- include router from `api.py`

\- startup seed if DB empty

\- health endpoint



Minimal behavior:



```python

app = FastAPI(title="DhakaPath Backend")

app.include\_router(api\_router)

```



Startup:



```python

@app.on\_event("startup")

def startup():

&#x20;   create\_db\_and\_tables()

&#x20;   if db\_is\_empty():

&#x20;       seed\_demo\_data()

```



\---



\## `app/config.py`



Load env:



```text

DATABASE\_URL

JWT\_SECRET

JWT\_ALGORITHM

ACCESS\_TOKEN\_EXPIRE\_MINUTES

OPENROUTER\_API\_KEY

GEMINI\_API\_KEY

GROQ\_API\_KEY

CORS\_ORIGINS

DEMO\_SECRET

UPLOAD\_DIR

```



\---



\## `app/db.py`



SQLModel engine:



```python

engine = create\_engine(settings.DATABASE\_URL)

```



Use SQLite file:



```text

sqlite:///./data/dhakapath.db

```



\---



\## `app/security.py`



Handle:



\- password hash

\- verify password

\- create JWT

\- decode JWT



Roles:



```text

user

admin

```



User field:



```text

is\_verified boolean

```



Report permission:



```text

authenticated AND is\_verified == true

```



Admin permission:



```text

role == admin

```



\---



\## `app/deps.py`



FastAPI dependencies:



```python

get\_current\_user

get\_current\_verified\_user

get\_current\_admin

get\_db

```



\---



\## `app/api\_catalog.py`



This is the one file for frontend/backend connection.



It should contain all endpoint paths as constants.



Example:



```python

ENDPOINTS = {

&#x20;   "health": {

&#x20;       "method": "GET",

&#x20;       "path": "/health"

&#x20;   },

&#x20;   "login": {

&#x20;       "method": "POST",

&#x20;       "path": "/api/auth/login"

&#x20;   },

&#x20;   "transit\_network": {

&#x20;       "method": "GET",

&#x20;       "path": "/api/transit/network"

&#x20;   },

&#x20;   "trip\_search": {

&#x20;       "method": "POST",

&#x20;       "path": "/api/trips/search"

&#x20;   },

&#x20;   "chat": {

&#x20;       "method": "POST",

&#x20;       "path": "/api/chat"

&#x20;   },

&#x20;   "events": {

&#x20;       "method": "GET",

&#x20;       "path": "/api/events"

&#x20;   }

}

```



Also expose:



```http

GET /api/catalog

```



so frontend can fetch endpoint list at runtime.



Actual route implementations live in:



```text

app/api.py

```



But endpoint names/paths are centralized in:



```text

app/api\_catalog.py

```



\---



\# 7. Database Models



Use SQLite tables.



\## `users`



```text

id

email

password\_hash

name

role

is\_verified

created\_at

```



Roles:



```text

user

admin

```



Seed:



```text

admin@dhakapath.local

user1@dhakapath.local

user2@dhakapath.local

user3@dhakapath.local

```



All demo users verified.



\---



\## `hubs`



```text

hub\_id

name

name\_bn

aliases\_json

latitude

longitude

type

created\_at

updated\_at

```



Types:



```text

hub

metro\_station

interchange

landmark

```



Seed 14–15 hubs:



```text

Motijheel

Banasree

Malibagh

Shahbagh

Farmgate

Science Lab

Dhanmondi

Mohammadpur

Mirpur 10

Agargaon

Mohakhali

Badda

Uttara

Jatrabari

Kazipara

```



Aliases example:



```json

\["Mirpur-10", "Mirpur 10 Super Market"]

```



\---



\## `routes`



This is the transit graph edge table.



```text

route\_id

mode

origin\_hub

destination\_hub

fare\_ranges\_json

student\_eligible

estimated\_time\_min

boarding\_notes\_json

alighting\_notes\_json

status

closed\_reason

flood\_prone

one\_way

tags\_json

created\_at

updated\_at

```



`fare\_ranges\_json` example:



```json

{

&#x20; "baseline": \[30, 30],

&#x20; "rush": \[30, 30],

&#x20; "rain": \[30, 30]

}

```



For rickshaw:



```json

{

&#x20; "baseline": \[30, 50],

&#x20; "rush": \[50, 80],

&#x20; "rain": \[60, 100]

}

```



For MRT:



```json

{

&#x20; "baseline": \[40, 40],

&#x20; "rush": \[40, 40],

&#x20; "rain": \[40, 40]

}

```



For walk:



```json

{

&#x20; "baseline": \[0, 0],

&#x20; "rush": \[0, 0],

&#x20; "rain": \[0, 0]

}

```



Status:



```text

active

closed

```



\---



\## `fare\_rules`



```text

mode

student\_discount\_percent

minimum\_student\_fare\_bdt

surge\_allowed

rush\_time\_multiplier

rain\_time\_multiplier

updated\_at

```



Examples:



```text

bus: 50%, min 10, surge false

mrt: 0%, min null, surge false

rickshaw: 0%, min null, surge true

cng: 0%, min null, surge true

leguna: configurable

walk: 0%, fare 0

```



\---



\## `transfer\_rules`



Optional but useful for correct duration.



```text

from\_mode

to\_mode

wait\_min

```



Examples:



```text

bus → mrt: 7

mrt → bus: 7

bus → bus: 5

rickshaw → mrt: 5

mrt → rickshaw: 5

walk → any: 0

any → walk: 0

```



If you want faster build, hardcode this in `fare\_service.py` or `routing\_service.py` as a dict.



\---



\## `reports`



```text

report\_id

report\_type

severity

affected\_hub

affected\_route\_ids\_json

description

mode

fare\_amount\_bdt

reported\_by

incident\_time

created\_at

last\_verified\_at

upvotes

downvotes

confidence

freshness

half\_life\_min

expires\_at

status

edge\_closed

extra\_time\_min

needs\_admin\_review

contradiction\_flag

created\_by\_session

```



Status:



```text

draft

pending

verified

rejected

resolved

expired

```



Severity:



```text

light

moderate

heavy

critical

```



\---



\## `report\_votes`



```text

id

report\_id

user\_id

value

created\_at

```



Constraints:



```text

unique(report\_id, user\_id)

value in (-1, 1)

```



Reporter cannot vote own report.



\---



\## `report\_drafts`



For chat draft flow.



```text

draft\_id

session\_id

user\_id

payload\_json

status

created\_at

submitted\_at

```



Status:



```text

draft

submitted

cancelled

expired

```



\---



\## `simulations`



Per-session scenario state.



```text

scenario\_id

session\_id

scenario\_type

zone\_hub

status

effects\_json

created\_at

expires\_at

```



Status:



```text

active

reset

expired

```



Effects JSON example:



```json

{

&#x20; "closed\_route\_ids": \["ROAD\_SHAHBAGH\_SCIENCE\_LAB"],

&#x20; "time\_multiplier": 1.3,

&#x20; "fare\_scenario": "rain",

&#x20; "extra\_transfer\_wait\_min": 5

}

```



\---



\## `events`



For live demo feed.



```text

event\_id

event\_type

session\_id

payload\_json

created\_at

```



Event types:



```text

report.created

report.draft.created

report.draft.submitted

report.confirmed

report.downvoted

report.verified

report.rejected

report.resolved

report.expired

simulation.applied

simulation.reset

route.recalculated

grounding.blocked

chat.message

```



\---



\## `chat\_sessions`



```text

session\_id

user\_id

created\_at

```



`user\_id` nullable for guest chat.



\---



\## `chat\_messages`



```text

id

session\_id

role

content

intent\_json

entities\_json

payload\_json

grounding\_json

created\_at

```



Roles:



```text

user

assistant

system

```



\---



\## `chat\_context`



```text

session\_id

context\_state\_json

updated\_at

```



\---



\## `llm\_cache`



Optional but highly recommended for demo stability.



```text

id

prompt\_hash

provider

response\_json

created\_at

```



Use cache for repeated demo prompts.



\---



\# 8. API Endpoint Catalog



Put this in `app/api\_catalog.py`.



\## Public endpoints



```text

GET  /health

GET  /api/catalog



POST /api/auth/register        # optional, can auto-verify demo users

POST /api/auth/login

GET  /api/auth/me



GET  /api/transit/network

GET  /api/transit/hub-statuses

GET  /api/transit/routes

GET  /api/transit/hubs



POST /api/trips/search

POST /api/fares/estimate



GET  /api/reports

GET  /api/reports/{report\_id}



GET  /api/events



POST /api/chat

GET  /api/chat/sessions/{session\_id}

```



\---



\## Verified user endpoints



```text

POST /api/reports

POST /api/reports/drafts/{draft\_id}/submit

POST /api/reports/drafts/{draft\_id}/cancel



POST /api/reports/{report\_id}/vote

POST /api/reports/{report\_id}/confirm

POST /api/reports/{report\_id}/resolve



POST /api/simulations

POST /api/simulations/reset

GET  /api/simulations/active

```



\---



\## Admin endpoints



```text

POST /api/admin/reports/{report\_id}/verify

POST /api/admin/reports/{report\_id}/reject

POST /api/admin/reports/{report\_id}/reopen



POST /api/admin/routes

PATCH /api/admin/routes/{route\_id}

POST /api/admin/routes/{route\_id}/block

POST /api/admin/routes/{route\_id}/unblock



GET  /api/admin/fare-rules

PUT  /api/admin/fare-rules/{mode}



PUT  /api/admin/connector-ranges/{mode}



POST /api/admin/hubs

PATCH /api/admin/hubs/{hub\_id}

```



\---



\## Grounding/demo endpoints



```text

POST /api/grounding/check

POST /api/grounding/fake-test

POST /api/events/publish-debug   # optional, protected by DEMO\_SECRET

```



\---



\# 9. Unified Response Envelope



Every backend response uses:



```json

{

&#x20; "ok": true,

&#x20; "data": {},

&#x20; "meta": {

&#x20;   "request\_id": "req\_xxx",

&#x20;   "timestamp": "2026-10-16T10:30:00Z",

&#x20;   "source": "dhakapath-backend"

&#x20; },

&#x20; "errors": \[]

}

```



Error:



```json

{

&#x20; "ok": false,

&#x20; "data": null,

&#x20; "meta": {

&#x20;   "request\_id": "req\_xxx",

&#x20;   "timestamp": "2026-10-16T10:30:00Z",

&#x20;   "source": "dhakapath-backend"

&#x20; },

&#x20; "errors": \[

&#x20;   {

&#x20;     "code": "UNKNOWN\_HUB",

&#x20;     "message": "Hub not found",

&#x20;     "field": "origin",

&#x20;     "details": {}

&#x20;   }

&#x20; ]

}

```



Use this for all endpoints.



\---



\# 10. Core Service Logic



\## A. `transit\_service.py`



Responsibilities:



\- get hubs

\- get routes

\- get fare rules

\- compute derived hub status

\- build graph for routing



Important function:



```python

def get\_active\_graph(session\_id: str | None) -> Graph:

&#x20;   routes = get\_active\_routes()

&#x20;   alerts = get\_routing\_impacting\_alerts()

&#x20;   scenario = get\_active\_scenario(session\_id)



&#x20;   apply alert closures

&#x20;   apply scenario closures

&#x20;   apply scenario time multipliers

&#x20;   apply scenario fare scenario



&#x20;   return graph

```



Routing impact sources:



```text

route.status == closed

verified alert edge\_closed == true

active session simulation closed\_route\_ids

```



\---



\## B. `routing\_service.py`



Responsibilities:



\- resolve origin/destination

\- build graph

\- run multi-objective search

\- return route options

\- ensure at least 3 options when possible



Main function:



```python

def search\_routes(

&#x20;   origin\_text: str,

&#x20;   destination\_text: str,

&#x20;   budget\_bdt: int | None,

&#x20;   prefer\_student\_fare: bool,

&#x20;   avoid\_hubs: list\[str],

&#x20;   avoid\_modes: list\[str],

&#x20;   optimize: str,

&#x20;   session\_id: str | None

) -> dict:

```



Flow:



```text

1\. resolve hubs

2\. load active graph with session scenario

3\. filter edges

4\. run cheapest search

5\. run fastest search

6\. run fewest transfers search

7\. run balanced search

8\. deduplicate

9\. if less than 3, penalize used edges and rerun

10\. calculate fares using fare\_service

11\. calculate durations using transfer\_rules

12\. apply budget warnings

13\. ground result

14\. return routes

```



Route option response must include:



```json

{

&#x20; "route\_option\_id": "OPT\_CHEAPEST\_001",

&#x20; "rank\_label": "cheapest",

&#x20; "total\_regular\_fare\_min\_bdt": 70,

&#x20; "total\_regular\_fare\_max\_bdt": 70,

&#x20; "total\_student\_fare\_min\_bdt": 55,

&#x20; "total\_student\_fare\_max\_bdt": 55,

&#x20; "total\_travel\_time\_min": 40,

&#x20; "total\_transfer\_wait\_min": 10,

&#x20; "total\_walking\_time\_min": 5,

&#x20; "total\_duration\_min": 55,

&#x20; "transfers": 1,

&#x20; "within\_budget": true,

&#x20; "disruption\_risk": "low",

&#x20; "legs": \[],

&#x20; "warnings": \[],

&#x20; "grounding": {

&#x20;   "valid": true

&#x20; }

}

```



Leg response:



```json

{

&#x20; "order": 1,

&#x20; "mode": "bus",

&#x20; "route\_id": "BUS\_MIRPUR10\_FARMGATE",

&#x20; "from\_hub": "MIRPUR\_10",

&#x20; "to\_hub": "FARMGATE",

&#x20; "regular\_fare\_min\_bdt": 30,

&#x20; "regular\_fare\_max\_bdt": 30,

&#x20; "student\_fare\_min\_bdt": 15,

&#x20; "student\_fare\_max\_bdt": 15,

&#x20; "travel\_time\_min": 25,

&#x20; "transfer\_wait\_after\_min": 7,

&#x20; "boarding\_instruction": "Board near Mirpur 10 Super Market bus stand.",

&#x20; "alighting\_instruction": "Alight at Farmgate Bus Stand.",

&#x20; "transfer\_instruction": "Walk to Farmgate MRT entrance."

}

```



\---



\## C. `fare\_service.py`



Responsibilities:



\- calculate leg fare

\- calculate student fare

\- apply scenario range

\- calculate total min/max

\- validate fare sanity



Main functions:



```python

def calculate\_leg\_fare(route, scenario\_type, prefer\_student\_fare) -> FareResult

def calculate\_trip\_fare(legs, scenario\_type, prefer\_student\_fare) -> TripFare

def validate\_submitted\_fare(mode, fare\_amount, route\_id=None) -> SanityResult

```



Student bus rule:



```python

def bus\_student\_fare(base\_fare: int) -> int:

&#x20;   discounted = math.ceil(base\_fare \* 0.5)

&#x20;   return max(10, discounted)

```



Examples:



```text

base 30 → student 15

base 25 → student 13

base 15 → student 10

base 10 → student 10

```



Connector range selection:



```text

no scenario: baseline

peak\_rush\_hour: rush

heavy\_rain: rain

both rain+rush: use max(rush, rain), optionally add small combined penalty

```



Safer rule:



```text

if rain and rush:

&#x20; min = max(rush\_min, rain\_min)

&#x20; max = max(rush\_max, rain\_max)

```



Total fare:



```python

regular\_min = sum(leg.regular\_fare\_min)

regular\_max = sum(leg.regular\_fare\_max)



student\_min = sum(leg.student\_fare\_min)

student\_max = sum(leg.student\_fare\_max)

```



If mode has no student discount:



```python

student\_fare\_min = regular\_fare\_min

student\_fare\_max = regular\_fare\_max

```



\---



\## D. `alert\_service.py`



Responsibilities:



\- create report

\- create draft from chat

\- submit draft

\- votes

\- confirm

\- resolve

\- admin verify/reject/reopen

\- decay refresh

\- sanity checks

\- contradiction checks

\- routing impacting alerts query



Main functions:



```python

def create\_report(user, payload) -> Report

def create\_draft(session\_id, user\_id, payload) -> Draft

def submit\_draft(draft\_id, user) -> Report

def vote\_report(report\_id, user, value) -> Report

def confirm\_report(report\_id, user) -> Report

def resolve\_report(report\_id, user) -> Report

def admin\_verify(report\_id, admin) -> Report

def admin\_reject(report\_id, admin, reason) -> Report

def refresh\_report\_decay(report) -> Report

def get\_routing\_impacting\_alerts() -> list\[Alert]

```



Vote rules:



```text

one vote per user per report

reporter cannot vote own report

upvote +1

downvote -1

vote updates last\_verified\_at only if upvote? yes, confirm/upvote refreshes freshness

```



Recommended:



```text

upvote: last\_verified\_at = now

downvote: does not refresh freshness

```



Confidence:



```python

severity\_base = {

&#x20; "light": 0.25,

&#x20; "moderate": 0.35,

&#x20; "heavy": 0.45,

&#x20; "critical": 0.55

}



vote\_score = 0.05 \* upvotes - 0.08 \* downvotes

admin\_bonus = 0.20 if status == verified else 0

base\_confidence = severity\_base\[severity] + vote\_score + admin\_bonus



age\_min = (now - last\_verified\_at).minutes

decay\_factor = 0.5 \*\* (age\_min / half\_life\_min)



confidence = clamp(base\_confidence \* decay\_factor, 0, 1)

freshness = decay\_factor

expires\_at = last\_verified\_at + timedelta(minutes=half\_life\_min \* 2)

```



Auto expire:



```text

if status == verified and (confidence < 0.25 or now > expires\_at):

&#x20;   status = expired

&#x20;   write event report.expired

```



Routing impact:



```text

status == verified

AND confidence >= 0.40

AND now < expires\_at

```



Contradiction check:



Examples:



\- active verified `intersection\_blockade` at hub and new `all\_clear` report

\- active verified `waterlogging heavy` and new `all\_clear`

\- same route has verified closed alert and verified open/all-clear alert



Action:



```text

new report status = pending

needs\_admin\_review = true

contradiction\_flag = true

does not affect routing until admin resolves

```



Duplicate check:



```text

same user + same hub + same report\_type within 10 minutes

```



Action:



```text

reject duplicate or convert into confirmation of existing report

```



For simplicity:



```text

return existing report and add upvote if user different

```



But if same user:



```text

reject duplicate

```



\---



\## E. `simulation\_service.py`



Responsibilities:



\- apply scenario per session

\- reset scenario

\- get active scenario

\- produce before/after comparison



Main functions:



```python

def apply\_simulation(session\_id, scenario\_type, zone\_hub, user=None) -> SimulationResult

def reset\_simulation(session\_id) -> Result

def get\_active\_scenario(session\_id) -> Scenario | None

def get\_scenario\_effects(session\_id) -> ScenarioEffects

```



Scenario effects:



```json

{

&#x20; "closed\_route\_ids": \[],

&#x20; "time\_multiplier": 1.0,

&#x20; "fare\_scenario": "baseline",

&#x20; "extra\_transfer\_wait\_min": 0,

&#x20; "affected\_hubs": \[]

}

```



Heavy rain:



```text

fare\_scenario = rain

time\_multiplier = 1.25

close flood\_prone routes near zone\_hub

```



Peak rush:



```text

fare\_scenario = rush

time\_multiplier = 1.30

extra\_transfer\_wait\_min = 5

```



Blocked intersection:



```text

close all active routes where origin\_hub == zone\_hub or destination\_hub == zone\_hub

except walk maybe

```



Before/after:



```json

{

&#x20; "before": {

&#x20;   "route\_option\_id": "OPT\_CHEAPEST\_001",

&#x20;   "total\_duration\_min": 55,

&#x20;   "total\_regular\_fare\_min\_bdt": 70,

&#x20;   "total\_regular\_fare\_max\_bdt": 70

&#x20; },

&#x20; "after": {

&#x20;   "route\_option\_id": "OPT\_DETOUR\_001",

&#x20;   "total\_duration\_min": 75,

&#x20;   "total\_regular\_fare\_min\_bdt": 85,

&#x20;   "total\_regular\_fare\_max\_bdt": 105

&#x20; }

}

```



Important:



Simulation must not globally mutate `routes.status`.



It should store session scenario and apply virtually during graph build.



Admin road block is different:



```text

admin block changes route.status = closed globally

```



Simulation is temporary/session-based.



\---



\## F. `grounding\_service.py`



Responsibilities:



\- validate AI-generated or assistant-facing route payloads

\- block fake routes/fares

\- produce grounding badge



Main functions:



```python

def validate\_route\_payload(payload) -> GroundingResult

def validate\_chat\_response(chat\_response) -> GroundingResult

def fake\_grounding\_test(payload) -> GroundingResult

```



Checks:



```text

all hub\_id exist

all route\_id exist

all fare values within computed min/max

all durations match engine

all alert\_id exist

student fare rule valid

no nonexistent direct route invented

```



Response:



```json

{

&#x20; "grounded": true,

&#x20; "safe\_for\_display": true,

&#x20; "violations": \[],

&#x20; "checked\_route\_ids": \[],

&#x20; "checked\_hub\_ids": \[],

&#x20; "fare\_check\_passed": true

}

```



If failed:



```json

{

&#x20; "grounded": false,

&#x20; "safe\_for\_display": false,

&#x20; "violations": \[

&#x20;   {

&#x20;     "code": "UNKNOWN\_ROUTE\_ID",

&#x20;     "message": "Route does not exist",

&#x20;     "offending\_value": "BUS\_MIRPUR\_MOTIJHEEL\_DIRECT"

&#x20;   }

&#x20; ],

&#x20; "fallback\_used": true

}

```



Judge demo:



```http

POST /api/grounding/fake-test

```



Input:



```json

{

&#x20; "route\_ids": \["BUS\_MIRPUR\_MOTIJHEEL\_DIRECT"],

&#x20; "claimed\_fare\_bdt": 25

}

```



Output:



```json

{

&#x20; "grounded": false,

&#x20; "blocked": true,

&#x20; "violations": \[

&#x20;   "UNKNOWN\_ROUTE\_ID",

&#x20;   "FARE\_MISMATCH"

&#x20; ]

}

```



\---



\## G. `nlp\_service.py`



Responsibilities:



\- parse English/Banglish

\- resolve hub aliases

\- extract intent/entities

\- fallback rule parser



Main function:



```python

def parse\_message(text: str, context: dict) -> ParsedIntent

```



Intents:



```text

find\_route

request\_alternative\_route

submit\_disruption\_report

confirm\_report

explain\_fare\_rule

alert\_reliability

compare\_fare

smalltalk

unknown

```



Entity example:



```json

{

&#x20; "intent": "find\_route",

&#x20; "origin\_text": "Mirpur 10",

&#x20; "destination\_text": "Motijheel",

&#x20; "budget\_bdt": 80,

&#x20; "prefer\_student\_fare": true,

&#x20; "avoid\_hubs\_text": \["Shahbagh"]

}

```



Hub resolution:



```text

exact match

alias match

normalized match

fuzzy match

suggest candidates if uncertain

```



Normalization:



```text

lowercase

remove punctuation

collapse spaces

convert Banglish variants

```



Banglish examples:



```text

theke → from

thekay → from

jayga → place

pani joma → waterlogging

jam → gridlock

block → blockade

taka/tk → BDT

student fare → prefer\_student\_fare

avoid koro → avoid\_hubs

```



Fallback rule parser:



If LLM fails:



```text

if text contains "theke" or "to" or "jete chai" → find\_route

if text contains "pani" or "waterlog" → submit\_disruption\_report waterlogging

if text contains "block" → intersection\_blockade

if text contains "student" → prefer\_student\_fare

if text contains "avoid" → avoid\_hubs

extract numbers as budget/fare

```



\---



\## H. `llm\_service.py`



Responsibilities:



\- call OpenRouter/Gemini/Groq

\- enforce JSON output

\- cache responses

\- fallback provider

\- fallback rule parser



Main function:



```python

def extract\_intent(message: str, context: dict) -> dict

```



Optional:



```python

def generate\_reply\_text(intent: str, engine\_payload: dict, language: str) -> str

```



But safer:



Use template reply text by default.



Only use LLM to polish if needed.



LLM must receive only structured engine facts, not ask to invent.



Prompt rule:



```text

You are a formatter only.

Use only provided facts.

Do not add routes, fares, hubs, or alerts.

Return JSON.

```



Cache:



```text

hash(message + context\_summary + provider)

```



Demo benefit:



Rehearsal prompts cached, so live demo does not fail.



\---



\## I. `chat\_service.py`



Responsibilities:



\- maintain session context

\- call nlp\_service

\- call engines

\- ground response

\- return frontend-ready payload



Main function:



```python

def handle\_chat(session\_id, message, user, ui\_language) -> ChatResponse

```



Flow:



```text

1\. get/create chat session

2\. load context\_state

3\. parse intent using LLM/rule fallback

4\. resolve entities

5\. branch by intent

6\. call appropriate service

7\. build structured\_payload

8\. build reply\_text using template or grounded LLM

9\. run grounding check

10\. save message and context

11\. write event chat.message

12\. return response

```



Chat response format:



```json

{

&#x20; "session\_id": "CHAT\_SESSION\_001",

&#x20; "message\_id": "MSG\_AI\_001",

&#x20; "reply\_text": "...",

&#x20; "language\_detected": "banglish",

&#x20; "intent": "find\_route",

&#x20; "entities": {},

&#x20; "actions": \[],

&#x20; "structured\_payload": {},

&#x20; "grounding": {

&#x20;   "checked": true,

&#x20;   "valid": true,

&#x20;   "violations": \[]

&#x20; },

&#x20; "context\_state": {},

&#x20; "suggested\_followups": \[]

}

```



Use `reply\_text`, not `reply.text`.



\---



\### Chat intent behaviors



\#### find\_route



Call:



```text

routing\_service.search\_routes()

```



Return:



```json

{

&#x20; "routes": \[],

&#x20; "selected\_route\_id": ""

}

```



\#### request\_alternative\_route



Use context last origin/destination.



Add avoid hub from message or context.



Call route search.



\#### submit\_disruption\_report



Create draft only.



Return:



```json

{

&#x20; "draft\_report": {},

&#x20; "confirmation\_required": true

}

```



Do not create pending report yet.



\#### confirm\_report



If user says confirm and context has draft:



Call:



```text

alert\_service.submit\_draft()

```



Report becomes pending.



\#### explain\_fare\_rule



Read:



```text

fare\_rules

```



Return fare rule card.



\#### alert\_reliability



Read report and compute:



```text

confidence

freshness

votes

expires\_at

admin\_approved

affects\_routing

reasons

```



Return reliability payload.



\---



\## J. `event\_service.py`



Responsibilities:



\- write events

\- return events since cursor

\- filter by session if needed



Main functions:



```python

def publish\_event(event\_type, payload, session\_id=None) -> Event

def get\_events(since\_id=0, session\_id=None, limit=100) -> list\[Event]

```



Event response:



```json

{

&#x20; "event\_id": 42,

&#x20; "event\_type": "report.verified",

&#x20; "session\_id": "DEMO\_SESSION",

&#x20; "payload": {},

&#x20; "created\_at": "2026-10-16T10:30:00Z"

}

```



Frontend polls:



```text

/api/events?since\_id=42\&session\_id=DEMO\_SESSION

```



\---



\# 11. Dummy Live Demo Python Script



Create:



```text

app/scripts/demo\_logs.py

```



Purpose:



Simulate live commuter activity for demo.



It should call real backend endpoints, not directly modify DB, so backend logic and events are exercised.



Run:



```bash

python -m app.scripts.demo\_logs --base-url http://localhost:8000 --session DEMO\_SESSION --interval 4

```



Demo sequence:



```text

1\. Login user1

2\. Chat: report Shahbagh waterlogging

3\. Backend creates draft

4\. User confirms draft

5\. Report becomes pending

6\. Login user2

7\. User2 confirms/upvotes

8\. Login user3

9\. User3 confirms/upvotes

10\. Login admin

11\. Admin verifies report

12\. Event: report.verified

13\. Frontend sees alert becomes verified

14\. Apply heavy rain simulation for DEMO\_SESSION

15\. Route search before/after

16\. Show detour

17\. User marks resolved

18\. Admin reopens or reset simulation

19\. Grounding fake test blocks fake AI route

```



Script prints logs:



```text

\[DEMO] Creating draft report...

\[DEMO] Submitting draft...

\[DEMO] User2 confirmed...

\[DEMO] Admin verified...

\[DEMO] Applying heavy rain...

\[DEMO] Route recalculated...

```



Backend stores events, frontend polls `/api/events`.



This satisfies:



```text

backend catches logs and requests from both frontend and backend script and sends output to frontend

```



Actually backend receives HTTP requests from script, processes them, stores events, and frontend consumes events.



\---



\# 12. Seed Data Requirements



Seed must be realistic enough for demo.



\## Hubs



14–15 hubs.



\## Routes



30–40 edges.



Include:



\- bus routes

\- MRT-6 segments

\- leguna routes

\- rickshaw connectors

\- CNG connectors

\- walking connectors



Need redundancy so route search can return 3 options.



Suggested network pattern:



```text

Mirpur 10 ↔ Farmgate

Mirpur 10 ↔ Mohammadpur

Mirpur 10 ↔ Kazipara

Mohammadpur ↔ Dhanmondi

Dhanmondi ↔ Farmgate

Farmgate ↔ Motijheel

Farmgate ↔ Shahbagh

Shahbagh ↔ Science Lab

Science Lab ↔ Motijheel

Uttara ↔ Agargaon

Agargaon ↔ Mohakhali

Mohakhali ↔ Farmgate

Badda ↔ Malibagh

Malibagh ↔ Shahbagh

Banasree ↔ Malibagh

Jatrabari ↔ Motijheel

```



Add alternate rickshaw/CNG connectors between major hubs.



\## Fare ranges



Examples:



Bus:



```json

{"baseline":\[30,30],"rush":\[30,30],"rain":\[30,30]}

```



MRT:



```json

{"baseline":\[40,40],"rush":\[40,40],"rain":\[40,40]}

```



Rickshaw:



```json

{"baseline":\[30,50],"rush":\[50,80],"rain":\[60,100]}

```



CNG:



```json

{"baseline":\[40,70],"rush":\[70,100],"rain":\[80,120]}

```



Walk:



```json

{"baseline":\[0,0],"rush":\[0,0],"rain":\[0,0]}

```



\## Alerts



Seed 3–4 reports:



```text

one pending

one verified

one rejected

one resolved/expired

```



\## Users



```text

admin@dhakapath.local / password

user1@dhakapath.local / password

user2@dhakapath.local / password

user3@dhakapath.local / password

```



All verified.



\---



\# 13. Critical Backend Flow Examples



\## Flow 1: Public route search



Request:



```json

{

&#x20; "origin": "Mirpur 10",

&#x20; "destination": "Motijheel",

&#x20; "budget\_bdt": 80,

&#x20; "prefer\_student\_fare": true,

&#x20; "avoid\_hubs": \["Shahbagh"],

&#x20; "session\_id": "DEMO\_SESSION"

}

```



Backend:



```text

resolve hubs

load graph

apply session scenario

apply verified alerts

filter avoided hubs

run route searches

calculate fares

calculate durations

ground routes

return 3+ options

```



Response route:



```json

{

&#x20; "route\_option\_id": "OPT\_CHEAPEST\_001",

&#x20; "rank\_label": "cheapest",

&#x20; "total\_regular\_fare\_min\_bdt": 70,

&#x20; "total\_regular\_fare\_max\_bdt": 70,

&#x20; "total\_student\_fare\_min\_bdt": 55,

&#x20; "total\_student\_fare\_max\_bdt": 55,

&#x20; "total\_travel\_time\_min": 40,

&#x20; "total\_transfer\_wait\_min": 10,

&#x20; "total\_walking\_time\_min": 5,

&#x20; "total\_duration\_min": 55,

&#x20; "transfers": 1,

&#x20; "within\_budget": true,

&#x20; "legs": \[]

}

```



\---



\## Flow 2: Natural language search



Request:



```json

{

&#x20; "query\_text": "Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao",

&#x20; "session\_id": "DEMO\_SESSION"

}

```



Backend:



```text

nlp\_service.parse\_message()

→ intent find\_route

→ origin MIRPUR\_10

→ destination MOTIJHEEL

→ budget 80

→ student true

→ route search

```



\---



\## Flow 3: Chat report draft



User:



```text

Shahbagh e onek pani jome ache, bike chola jachhe na.

```



Backend:



```text

intent = submit\_disruption\_report

create draft\_report

status = draft

return confirmation\_required = true

```



Response:



```json

{

&#x20; "reply\_text": "Report draft taiyaar hoyeche. Confirm korle pending queue e jabe.",

&#x20; "intent": "submit\_disruption\_report",

&#x20; "structured\_payload": {

&#x20;   "draft\_report": {

&#x20;     "draft\_id": "DRAFT\_001",

&#x20;     "report\_type": "waterlogging",

&#x20;     "severity": "heavy",

&#x20;     "affected\_hub": "SHAHBAGH",

&#x20;     "status": "draft"

&#x20;   },

&#x20;   "confirmation\_required": true

&#x20; }

}

```



Then user confirms:



```http

POST /api/reports/drafts/DRAFT\_001/submit

```



Report becomes:



```text

status = pending

```



Admin verifies:



```http

POST /api/admin/reports/REPORT\_001/verify

```



Report becomes:



```text

status = verified

affects\_routing = true

```



\---



\## Flow 4: Simulation per session



Request:



```json

{

&#x20; "session\_id": "DEMO\_SESSION",

&#x20; "scenario\_type": "heavy\_rain",

&#x20; "zone\_hub": "MIRPUR\_10"

}

```



Backend:



```text

store simulation row

do not globally close routes

return affected segments

return recalculated detour routes for this session

```



Other sessions unaffected.



\---



\## Flow 5: Grounding fake AI route



Request:



```json

{

&#x20; "route\_ids": \["BUS\_MIRPUR\_MOTIJHEEL\_DIRECT"],

&#x20; "claimed\_fare\_bdt": 25

}

```



Backend:



```text

check route\_id exists → false

check fare matches DB → false

return blocked

```



Response:



```json

{

&#x20; "grounded": false,

&#x20; "blocked": true,

&#x20; "violations": \[

&#x20;   {

&#x20;     "code": "UNKNOWN\_ROUTE\_ID",

&#x20;     "offending\_value": "BUS\_MIRPUR\_MOTIJHEEL\_DIRECT"

&#x20;   },

&#x20;   {

&#x20;     "code": "FARE\_MISMATCH",

&#x20;     "offending\_value": 25

&#x20;   }

&#x20; ]

}

```



\---



\# 14. Tests You Must Have



For 1-hour build, write minimal but high-value tests.



\## Fare tests



Create `tests/test\_fares.py`.



Cases:



```text

1\. bus 30 → student 15

2\. bus 25 → student 13

3\. bus 15 → student 10 floor

4\. bus 10 → student 10 floor

5\. mrt 40 no student → student 40

6\. rickshaw baseline 30-50 no student → student 30-50

7\. rickshaw rain 60-100

8\. bus + mrt regular 70 student 55

9\. bus + rickshaw regular range, student only bus discounted

10\. submitted rickshaw fare 5000 → flagged unrealistic

```



\## Routing tests



Create `tests/test\_routing.py`.



Cases:



```text

1\. Mirpur 10 → Motijheel returns >=3 routes

2\. cheapest route fare <= fastest route fare usually

3\. avoid Shahbagh removes Shahbagh legs

4\. budget too low returns warning or no route suggestion

5\. closed edge forces detour

6\. heavy rain session changes fare range

7\. blocked intersection session closes hub edges

8\. student preference returns student fare fields

9\. all hub pairs smoke test returns route or valid no\_route\_reason

10\. route legs duration sums to total\_duration\_min

```



\## Alert tests



```text

1\. draft cannot affect routing

2\. pending cannot affect routing

3\. verified affects routing

4\. rejected does not affect routing

5\. resolved does not affect routing

6\. expired does not affect routing

7\. upvote increases confidence

8\. downvote decreases confidence

9\. old verified alert decays to expired

10\. contradictory report sets needs\_admin\_review

```



\## Grounding tests



```text

1\. valid route passes

2\. unknown route\_id fails

3\. unknown hub\_id fails

4\. fare mismatch fails

5\. fake direct bus fails

6\. chat route payload grounded true

7\. fallback used when AI invalid

```



\## Chat tests



Use 20–25 sentences.



Examples:



```text

Mirpur 10 theke Motijheel jete chai

Budget 60 takar moddhe route dao

Student fare hisebe koto lagbe?

Shahbagh avoid koro

Shahbagh e onek pani jome ache

Alternative route dao

Fastest ta dekho

Rickshaw fare ki surge hobe?

Alert ta koto reliable?

```



Backend should return structured JSON for each.



\---



\# 15. 1-Hour Backend Build Plan



This is aggressive but possible if you use LLM file-by-file and avoid scope creep.



\## Minute 0–5: Scaffold



Ask LLM to generate:



```text

requirements.txt

app/main.py

app/config.py

app/db.py

app/api.py

app/api\_catalog.py

.env.example

```



Must include:



\- FastAPI app

\- CORS

\- envelope helper

\- health endpoint

\- catalog endpoint



Acceptance:



```bash

uvicorn app.main:app --reload

```



returns:



```json

{"ok":true,"data":{"status":"healthy"}}

```



\---



\## Minute 5–15: DB models + seed



Ask LLM to generate:



```text

app/models.py

app/schemas.py

app/scripts/seed.py

```



Include tables:



```text

users

hubs

routes

fare\_rules

reports

report\_votes

report\_drafts

simulations

events

chat\_sessions

chat\_messages

chat\_context

llm\_cache

```



Seed:



```text

15 hubs

35 routes

fare rules

4 users

3 reports

```



Acceptance:



```bash

python -m app.scripts.seed --reset

```



creates DB and prints counts.



\---



\## Minute 15–25: Routing + fare engine



Ask LLM to generate:



```text

app/services/routing\_service.py

app/services/fare\_service.py

app/tests/test\_fares.py

app/tests/test\_routing.py

```



Implement:



\- Haversine

\- graph loader

\- A\*/Dijkstra

\- multi-objective search

\- 3 unique routes

\- fare min/max

\- student bus rule

\- transfer wait

\- duration sum



Acceptance:



```bash

pytest tests/test\_fares.py tests/test\_routing.py

```



Pass.



Manual:



```bash

curl -X POST http://localhost:8000/api/trips/search \\

\-H "Content-Type: application/json" \\

\-d '{"origin":"Mirpur 10","destination":"Motijheel"}'

```



Returns 3 routes.



\---



\## Minute 25–35: Alerts + simulation + grounding



Ask LLM to generate:



```text

app/services/alert\_service.py

app/services/simulation\_service.py

app/services/grounding\_service.py

app/tests/test\_alerts.py

app/tests/test\_grounding.py

```



Implement:



\- draft → pending

\- vote

\- confirm

\- resolve

\- admin verify/reject

\- decay

\- sanity

\- contradiction

\- session simulation

\- grounding fake test



Acceptance:



\- pending alert does not affect route

\- verified alert affects route

\- rain session changes fare range

\- fake route blocked



\---



\## Minute 35–45: Chat + NLP + LLM fallback



Ask LLM to generate:



```text

app/services/nlp\_service.py

app/services/llm\_service.py

app/services/chat\_service.py

app/tests/test\_chat.py

```



Implement:



\- intent JSON

\- hub alias resolution

\- context state

\- find\_route

\- submit\_report draft

\- confirm\_report

\- alternative\_route

\- fare\_rule

\- alert\_reliability

\- rule fallback

\- cache



Acceptance:



```json

{

&#x20; "session\_id": "s1",

&#x20; "message": "Mirpur 10 theke Motijheel, student fare"

}

```



returns routes and `reply\_text`.



\---



\## Minute 45–52: Events + demo script



Ask LLM to generate:



```text

app/services/event\_service.py

app/scripts/demo\_logs.py

```



Implement:



\- publish event on report/vote/admin/simulation/grounding

\- GET /api/events

\- demo script sequence



Acceptance:



Run backend:



```bash

uvicorn app.main:app --reload

```



Run script:



```bash

python -m app.scripts.demo\_logs --base-url http://localhost:8000 --session DEMO\_SESSION --interval 3

```



Then:



```bash

curl "http://localhost:8000/api/events?session\_id=DEMO\_SESSION"

```



returns events.



\---



\## Minute 52–60: API wiring + smoke test



Ask LLM to complete:



```text

app/api.py

```



All endpoints wired to services.



Run:



```bash

pytest

```



Fix only critical failures.



Prepare demo commands:



```bash

python -m app.scripts.seed --reset

uvicorn app.main:app --reload

python -m app.scripts.demo\_logs --session DEMO\_SESSION

```



\---



\# 16. What To Cut For 1-Hour Backend



Do not build these now unless core is finished:



Cut:



```text

ride-share

signboard OCR

comment system

image upload

admin application flow

PWA offline

WebSocket

complex analytics

real OSRM/Valhalla setup

heavy LangChain

production user verification workflow

```



Keep only:



```text

auth

public route search

fare engine

reports

votes

admin verify/reject

decay

simulation

grounding

chat

events

demo script

```



If time remains, add image upload as optional stub.



\---



\# 17. Final Backend Implementation Priority



Build in this order:



```text

1\. DB + seed

2\. route search + fare engine

3\. alerts + decay + admin verify

4\. simulation per session

5\. grounding

6\. chat

7\. events + demo script

8\. tests

```



Do not start with chat UI or LLM polish.



The scoring differentiator is:



```text

grounded routing + validated reports + live disruption recalculation

```



not fancy chat text.



\---



\# 18. Minimum Backend Demo Path



Backend must support this exact story:



```text

Student at Mirpur 10 wants to go Motijheel under budget with student fare.

System returns 3 multi-leg routes.

Heavy rain simulation applied to DEMO\_SESSION.

Rickshaw fare surges, flood-prone road closes.

System recalculates detour.

User reports Shahbagh waterlogging through chat.

Draft created.

User confirms draft.

Other users upvote.

Admin verifies.

Alert becomes routing-active.

Route search avoids/penalizes Shahbagh.

Judge asks: why trust this alert?

Backend returns reliability: votes, confidence, freshness, admin verified.

Judge tests fake AI route.

Grounding blocks it.

```



If backend can do this, you have covered Parts 1–5 strongly.

