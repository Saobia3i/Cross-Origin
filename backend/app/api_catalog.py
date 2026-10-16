"""Single source of truth for frontend <-> backend endpoints.

Frontend should discover endpoints via GET /api/catalog.
auth: public | verified_user | admin | debug_secret
"""
from typing import Any

PUBLIC, VERIFIED, ADMIN, DEBUG = "public", "verified_user", "admin", "debug_secret"

# (group, method, path, description, auth, implemented)
_E = [
    # Public
    ("public", "GET", "/health", "Health check.", PUBLIC, True),
    ("public", "GET", "/api/catalog", "Endpoint catalog.", PUBLIC, True),
    ("public", "POST", "/api/auth/register", "Register a user.", PUBLIC, True),
    ("public", "POST", "/api/auth/login", "Log in and receive a JWT.", PUBLIC, True),
    ("public", "GET", "/api/auth/me", "Current user profile.", PUBLIC, True),
    ("public", "GET", "/api/transit/network", "Full transit network: hubs, routes, fare rules, mode rates, multipliers.", PUBLIC, True),
    ("public", "GET", "/api/transit/hubs", "List hubs with derived status.", PUBLIC, True),
    ("public", "GET", "/api/transit/routes", "List route edges.", PUBLIC, True),
    ("public", "GET", "/api/transit/hub-statuses", "Derived hub statuses (normal/delayed/blocked).", PUBLIC, True),
    ("public", "POST", "/api/trips/search", "Search trips between two hubs.", PUBLIC, True),
    ("public", "POST", "/api/fares/estimate", "Fare for a cached route option or explicit legs (ranges).", PUBLIC, True),
    ("public", "GET", "/api/reports", "List reports.", PUBLIC, True),
    ("public", "GET", "/api/reports/{report_id}", "Get one report.", PUBLIC, True),
    ("public", "GET", "/api/reports/{report_id}/reliability", "Why an alert is trusted: votes, decay, approval.", PUBLIC, True),
    ("public", "GET", "/api/events", "Event stream / recent events.", PUBLIC, True),
    ("public", "POST", "/api/chat", "AI chat; returns reply_text, grounded.", PUBLIC, True),
    ("public", "GET", "/api/chat/sessions/{session_id}", "Chat session history.", PUBLIC, True),
    ("public", "GET", "/api/rideshare/posts", "List ride-share posts.", PUBLIC, True),
    # Verified user
    ("verified_user", "POST", "/api/reports", "Create a report draft.", VERIFIED, True),
    ("verified_user", "POST", "/api/reports/drafts/{draft_id}/submit", "Submit a draft.", VERIFIED, True),
    ("verified_user", "POST", "/api/reports/drafts/{draft_id}/cancel", "Cancel a draft.", VERIFIED, True),
    ("verified_user", "POST", "/api/reports/{report_id}/vote", "Vote on a report.", VERIFIED, True),
    ("verified_user", "DELETE", "/api/reports/{report_id}/vote", "Remove own vote.", VERIFIED, True),
    ("verified_user", "POST", "/api/reports/{report_id}/confirm", "Confirm a report.", VERIFIED, True),
    ("verified_user", "POST", "/api/reports/{report_id}/resolve", "Mark report resolved.", VERIFIED, True),
    ("verified_user", "POST", "/api/simulations", "Start a per-session simulation.", VERIFIED, True),
    ("verified_user", "POST", "/api/simulations/reset", "Reset the session simulation.", VERIFIED, True),
    ("verified_user", "GET", "/api/simulations/active", "Active simulation for this session.", VERIFIED, True),
    ("verified_user", "POST", "/api/rideshare/posts", "Create a ride-share post.", VERIFIED, True),
    ("verified_user", "POST", "/api/rideshare/posts/{post_id}/join", "Join a post.", VERIFIED, True),
    ("verified_user", "POST", "/api/rideshare/posts/{post_id}/leave", "Leave a post.", VERIFIED, True),
    ("verified_user", "POST", "/api/rideshare/posts/{post_id}/cancel", "Cancel own post.", VERIFIED, True),
    # Admin
    ("admin", "POST", "/api/admin/reports/{report_id}/verify", "Verify a report.", ADMIN, True),
    ("admin", "POST", "/api/admin/reports/{report_id}/reject", "Reject a report.", ADMIN, True),
    ("admin", "POST", "/api/admin/reports/{report_id}/reopen", "Reopen a report.", ADMIN, True),
    ("admin", "POST", "/api/admin/routes", "Create a route.", ADMIN, False),
    ("admin", "PATCH", "/api/admin/routes/{route_id}", "Update a route.", ADMIN, False),
    ("admin", "POST", "/api/admin/routes/{route_id}/block", "Close a route edge.", ADMIN, True),
    ("admin", "POST", "/api/admin/routes/{route_id}/unblock", "Reopen a route edge.", ADMIN, True),
    ("admin", "GET", "/api/admin/fare-rules", "List fare rules.", ADMIN, True),
    ("admin", "PUT", "/api/admin/fare-rules/{mode}", "Replace fare rule for a mode.", ADMIN, True),
    ("admin", "PATCH", "/api/admin/routes/{route_id}/fare", "Override a route fare.", ADMIN, False),
    ("admin", "POST", "/api/admin/hubs", "Create a hub.", ADMIN, False),
    ("admin", "GET", "/api/admin/users", "List users.", ADMIN, True),
    ("admin", "POST", "/api/admin/users/{user_id}/verify", "Verify a user so they can report and vote.", ADMIN, True),
    ("admin", "POST", "/api/admin/users/{user_id}/disable", "Disable a user account.", ADMIN, True),
    ("admin", "POST", "/api/admin/users/{user_id}/enable", "Re-enable a user account.", ADMIN, True),
    ("admin", "PATCH", "/api/admin/hubs/{hub_id}", "Update a hub.", ADMIN, False),
    # Grounding / debug
    ("debug", "POST", "/api/grounding/check", "Check text against grounded data.", DEBUG, True),
    ("debug", "POST", "/api/grounding/fake-test", "Grounding fake-claim test.", DEBUG, True),
    ("debug", "POST", "/api/events/publish-debug", "Publish a debug event.", DEBUG, True),
]

API_CATALOG: dict[str, Any] = {
    "version": "0.1.0",
    "auth_levels": [PUBLIC, VERIFIED, ADMIN, DEBUG],
    "endpoints": [
        {"group": g, "method": m, "path": p, "description": d, "auth": a, "implemented": impl}
        for g, m, p, d, a, impl in _E
    ],
}
