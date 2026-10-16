# Reports, Votes, Moderation & Events

Code: `app/services/alert_service.py` (workflow), `alert_rules.py` (decay maths), `event_service.py` (event log),
`app/api_reports.py` (endpoints), `app/deps.py` (auth). Tests: `test_alert_service.py`, `test_report_api.py`,
`test_event_service.py`.

## Authentication
`Authorization: Bearer <jwt>` (token `sub` = user id, minted by `app.security.create_access_token`). The user row is
re-read on every request. **There is no login/register endpoint yet**, so tokens must be minted by code until those are built.
Guests may browse (`GET`), not write. Errors: `UNAUTHORIZED` / `INVALID_TOKEN` / `TOKEN_EXPIRED` (401),
`NOT_VERIFIED` / `ACCOUNT_DISABLED` / `FORBIDDEN` (403). Set `JWT_SECRET` to 32+ bytes outside development.

## Lifecycle
```
draft (report_drafts) --submit--> pending --admin verify--> verified --resolve--> resolved
                                     |  \--reject--> rejected      \--expire--> expired      rejected/resolved/expired --admin reopen--> pending
```
Only an **admin** verifies, rejects or reopens. Votes never change the status. A verified user or an admin resolves a
verified report. Drafts (chat or frontend) become reports only through `POST /api/reports/drafts/{id}/submit`.
There is no endpoint to create a draft yet (chat will use `alert_service.create_draft`).

## Confidence, decay, routing
`base = severity_base + 0.05*up - 0.08*down + 0.20 if verified` (light .25, moderate .35, heavy .45, critical .55);
`decay = 0.5 ** (age_min / half_life)`; `confidence = clamp(base*decay, 0, 1)`; `freshness = decay`;
`expires_at = reference + 2*half_life`. **reference = `last_verified_at`, else `created_at`**. Half-lives: waterlogging 60,
severe_gridlock 30, intersection_blockade 30, road_construction 1440, fare_surge 45, accident 45, all_clear 60, other 60.
`community_score = upvotes - downvotes`.

A report **affects routing** only if: verified, confidence >= 0.40, now < `expires_at`, and it has an `affected_hub` or
route ids. The router recomputes this live on every search, so confidence decaying below 0.40 stops the effect even before
expiry. Pending, rejected, resolved, expired and draft reports never affect routing. A verified report changes routing
only through the effects its admin approved: `edge_closed` (closes its `affected_route_ids`) and `extra_time_min`
(delay); both default to none.

Decay and expiry are applied lazily whenever reports are read or acted on (no scheduler): expiring a report publishes
`report.expired` (+ `route.recalculated` if it was affecting routing).

## Sanity checks (on submit; `run_sanity_checks`)
- **Blocking (422/404):** incident time in the future (60 s grace) or older than 24 h; unknown `affected_hub`
  (names/aliases are resolved to the hub id); unknown route ids; `fare_surge` without `mode` + `fare_amount_bdt`.
- **Flags** (report is created `pending`, `needs_admin_review=true`, never routes until an admin acts):
  `duplicate` (same user + same hub, or same routes if no hub, + same type within 10 min),
  `contradiction` (`contradiction_flag=true`; an all_clear against an active verified waterlogging / blockade / gridlock at the
  same hub within 60 min, or against a verified closure on a shared route; or a hazard against a fresh verified all_clear),
  `unrealistic_fare` (`fare_service.validate_submitted_fare`: > 5x the expected maximum, or a wrong metro fare; skipped
  with a note when the distance is unknown).
- Admin verification re-runs the checks without the age/duplicate rules; flags are acknowledged (listed in the response
  `sanity`), hard errors still block. `needs_admin_review` also turns on when |community_score| >= 3
  (pending: net +3 or more asks for review; any active report: net -3 or less).

## Votes
One vote per user per report, +1 or -1; posting again changes it, `DELETE` retracts it. The reporter cannot vote or
confirm their own report (`CANNOT_VOTE_OWN_REPORT`). Only pending/verified reports are votable. An **upvote** (and
`confirm`, which is an upvote that publishes `report.confirmed`) sets `last_verified_at=now` and renews `expires_at`.
A **downvote** never refreshes. Retracting a vote or switching to a downvote does not undo an earlier refresh.

## Endpoints
| Endpoint | Who | Notes |
|---|---|---|
| `POST /api/reports` | verified user | JSON, or `multipart/form-data` (fields or a `payload` JSON string, up to 5 `images`) -> 201 `{report, sanity}` |
| `POST /api/reports/drafts/{id}/submit` / `/cancel` | verified user | submit -> 201 `{report, draft}` |
| `POST /api/reports/{id}/vote` `{value: 1\|-1}`, `DELETE .../vote`, `POST .../confirm` | verified user | |
| `POST /api/reports/{id}/resolve` | verified user or admin | verified -> resolved |
| `POST /api/admin/reports/{id}/verify` `{note?, edge_closed?, extra_time_min?}` | admin | pending -> verified |
| `POST /api/admin/reports/{id}/reject` `{reason}` / `/reopen` `{reason?}` | admin | |
| `GET /api/reports?status&report_type&affected_hub&since&limit` | public | newest first, limit 1-200 (default 50); adds `my_vote` when logged in |
| `GET /api/reports/{id}` | public | |
| `GET /api/events?since_id&session_id&limit&event_type` | public | `{events, next_since_id, has_more}` |
| `POST /api/events/publish-debug` | `DEMO_SECRET` (header `X-Demo-Secret` or body `secret`) | inserts one event only |

Report fields include `upvotes, downvotes, confidence, freshness, expires_at, last_verified_at, status, affects_routing,
needs_admin_review, contradiction_flag, my_vote, images[{url,mime,size_bytes}], flags`. Illegal moves return
`INVALID_STATUS_TRANSITION` (409).

## Images
Optional. JPEG, PNG or WebP (type, size and file signature are checked), at most 5 per report, 5 MB each; stored under
`data/uploads/reports/{report_id}/` and served from `/uploads/reports/{report_id}/{file}`. A rejected upload creates no report.

## Events
All 19 contract types are valid. This feature publishes `report.draft.created`, `report.draft.submitted`, `report.created`,
`report.voted`, `report.confirmed`, `report.verified`, `report.rejected`, `report.resolved`, `report.reopened`, `report.expired`
and `route.recalculated` (payload `{reason, report_id, affected_hub, affected_route_ids}`) when a change adds or removes a
routing effect. Events are written in the same transaction as the change. Draft events carry the draft's `session_id` and
are only visible to that session; report and route events are global. Cancelling a draft publishes nothing (no event type exists for it).
Payloads hold no personal data. `GET /api/events` is oldest-first; poll with `since_id = next_since_id`.
