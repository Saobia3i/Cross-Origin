"""Small in-memory cache of the most recent trip-search route options, so /api/fares/estimate can price
a `route_option_id` without the client resending its legs.

Option ids like OPT_CHEAPEST_001 repeat across searches, so entries are keyed by (session_id, option id)
and replaced by the next search of the same session. Process-local and bounded: it does not survive a
restart or span several workers; clients can always send `legs` instead.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

TTL_SECONDS = 30 * 60
MAX_ENTRIES = 500

_lock = threading.Lock()
_entries: "OrderedDict[tuple[str, str], tuple[float, dict]]" = OrderedDict()


def _key(session_id: str | None, option_id: str) -> tuple[str, str]:
    return (session_id or "", option_id)


def remember_options(session_id: str | None, options: list[dict]) -> None:
    now = time.monotonic()
    with _lock:
        # a new search replaces that session's earlier options
        for key in [k for k in _entries if k[0] == (session_id or "")]:
            del _entries[key]
        for o in options:
            _entries[_key(session_id, o["route_option_id"])] = (now, {
                "route_option_id": o["route_option_id"],
                "scenario": o.get("scenario", "normal"),
                "legs": [{"route_id": l["route_id"], "mode": l["mode"], "from_hub": l["from_hub"],
                          "to_hub": l["to_hub"]} for l in o["legs"]],
            })
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)


def recall_option(session_id: str | None, option_id: str) -> dict | None:
    with _lock:
        hit = _entries.get(_key(session_id, option_id))
        if hit is None:
            return None
        stamp, value = hit
        if time.monotonic() - stamp > TTL_SECONDS:
            del _entries[_key(session_id, option_id)]
            return None
        return value


def clear() -> None:
    with _lock:
        _entries.clear()
