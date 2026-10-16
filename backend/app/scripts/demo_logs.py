"""Live Dhaka-commute demo for judges. Drives a running backend through REAL HTTP endpoints only.

    python -m app.scripts.demo_logs --base-url http://localhost:8000 --session DEMO_SESSION --interval 3

Nothing is written to the database directly and no response is faked: every number printed here comes back from
the API. When the data needed for a step is missing the step says so and is skipped; the script never crashes.
Hub names come from GET /api/transit/hubs; the story prefers Mirpur 10 -> Motijheel and, if the seeded data lacks a
hub, falls back to a pair of hubs that really are connected (and prints that it did).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

PREFERRED_ORIGIN, PREFERRED_DESTINATION, PREFERRED_INCIDENT = "Mirpur 10", "Motijheel", "Shahbagh"
CHAT_ROUTE_TEXT = "Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao"
NO_DATA_MESSAGE = "Normalized/fixture transit data not available. Run seed --source fixtures for demo."
USERS = {"admin": "admin@dhakapath.local", "user1": "user1@dhakapath.local",
         "user2": "user2@dhakapath.local", "user3": "user3@dhakapath.local"}


def _mins(v) -> str:
    return f"{v} min" if v is not None else "duration n/a (no time data)"


class BackendUnreachable(Exception):
    pass


class Api:
    """Thin wrapper over an httpx-style client. Never raises on HTTP errors; returns (status, envelope)."""

    def __init__(self, client: Any, base_url: str = ""):
        self.client, self.base_url = client, base_url.rstrip("/")

    def call(self, method: str, path: str, token: str | None = None, json: Any = None, params: dict | None = None,
             headers: dict | None = None) -> tuple[int, dict]:
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        try:
            r = self.client.request(method, self.base_url + path, json=json, params=params, headers=h)
        except httpx.TransportError as exc:
            raise BackendUnreachable(str(exc)) from exc
        try:
            body = r.json()
        except ValueError:
            body = {"ok": False, "data": None, "errors": [{"code": "NON_JSON_RESPONSE", "message": r.text[:200]}]}
        return r.status_code, body


class Demo:
    def __init__(self, api: Api, session_id: str, interval: float, demo_secret: str, password: str,
                 out=print, sleep=time.sleep):
        self.api, self.session, self.interval, self.secret, self.password = api, session_id, interval, demo_secret, password
        self.out, self.sleep = out, sleep
        self.tokens: dict[str, str] = {}
        self.ctx: dict[str, Any] = {}
        self.results: dict[str, str] = {}

    # ------------------------------------------------------------------ output helpers
    def step(self, code: str, title: str) -> None:
        self.out(f"\n=== [{code}] {title} " + "=" * max(3, 60 - len(title)))

    def log(self, msg: str = "") -> None:
        self.out("  " + msg)

    def fail(self, code: str, what: str, status: int, body: dict) -> None:
        err = (body.get("errors") or [{}])[0]
        self.log(f"!! {what} failed: HTTP {status} {err.get('code', '')} {err.get('message', '')}".rstrip())
        self.results[code] = "FAILED"

    def pause(self) -> None:
        if self.interval > 0:
            self.sleep(self.interval)

    def data(self, status: int, body: dict) -> dict | list | None:
        return body.get("data") if status < 400 and body.get("ok") else None

    # ------------------------------------------------------------------ steps
    def login_all(self) -> None:
        self.step("0", "Log in as demo users and admin (POST /api/auth/login)")
        for key, email in USERS.items():
            status, body = self.api.call("POST", "/api/auth/login", json={"email": email, "password": self.password})
            d = self.data(status, body)
            if d:
                self.tokens[key] = d["access_token"]
                self.log(f"{key:5} ({email}) role={d['user']['role']} verified={d['user']['is_verified']}")
            else:
                self.fail("0", f"login {email}", status, body)
                self.log("   (seed the demo users first: python -m app.scripts.seed --source fixtures --reset)")

    def discover(self) -> bool:
        status, body = self.api.call("GET", "/api/transit/hubs")
        hubs = self.data(status, body) or []
        status, body = self.api.call("GET", "/api/transit/routes", params={"status": "active"})
        routes = self.data(status, body) or []
        self.ctx["hubs"], self.ctx["edges"] = hubs, routes
        if not hubs or not [r for r in routes if r.get("mode") != "walk"]:
            self.log(NO_DATA_MESSAGE)
            return False

        def find(name: str) -> dict | None:
            key = name.strip().lower()
            for h in hubs:
                if key in (h["hub_id"].lower(), h["name"].lower()) or key in [a.lower() for a in h.get("aliases", [])]:
                    return h
            return None

        by_id = {h["hub_id"]: h for h in hubs}
        neighbours: dict[str, list[str]] = {}
        for r in routes:
            if r.get("mode") == "walk":
                continue
            neighbours.setdefault(r["origin_hub"], []).append(r["destination_hub"])
            neighbours.setdefault(r["destination_hub"], []).append(r["origin_hub"])
        origin, dest, incident = find(PREFERRED_ORIGIN), find(PREFERRED_DESTINATION), find(PREFERRED_INCIDENT)
        if origin is None or dest is None:
            if origin is None:
                first = next(r for r in routes if r.get("mode") != "walk")
                origin = by_id[first["origin_hub"]]
            if dest is None or dest["hub_id"] == origin["hub_id"]:
                dest = by_id[neighbours[origin["hub_id"]][0]]
            self.log(f"NOTE: {PREFERRED_ORIGIN} -> {PREFERRED_DESTINATION} is not in the seeded data; "
                     f"using the connected pair {origin['name']} -> {dest['name']} instead.")
        self.ctx.update(origin=origin, dest=dest)
        if incident is None:
            incident = origin
            self.log(f"NOTE: hub '{PREFERRED_INCIDENT}' is not in the seeded data; the incident happens at {origin['name']}.")
        self.ctx["incident"] = incident
        return True

    def search(self, label: str, session: str | None, **extra) -> dict | None:
        o, d = self.ctx["origin"], self.ctx["dest"]
        body = {"origin": o["name"], "destination": d["name"], "session_id": session, **extra}
        status, resp = self.api.call("POST", "/api/trips/search", json={k: v for k, v in body.items() if v is not None})
        data = self.data(status, resp)
        if data is None:
            self.fail(label, "trip search", status, resp)
            return None
        return data

    def print_routes(self, data: dict) -> None:
        routes = data.get("routes") or []
        if not routes:
            reason = data.get("no_route_reason") or {}
            self.log(f"No route: {reason.get('code', '')} {reason.get('message', '')}".rstrip())
        for r in routes:
            chain = " > ".join(f"{l['mode']}:{l['from_hub']}->{l['to_hub']}" for l in r["legs"])
            fare = (f"student {r['total_student_fare_min_bdt']}-{r['total_student_fare_max_bdt']} BDT"
                    if r.get("total_student_fare_min_bdt") is not None else "fare unknown")
            self.log(f"[{r['rank_label']}] {fare}, {_mins(r.get('total_duration_min'))}, {r['transfers']} transfers, "
                     f"within_budget={r['within_budget']}, grounded={r['grounding']['valid']}")
            self.log(f"      {chain}")
        g = data.get("grounding") or {}
        self.log(f"grounding: {g.get('badge')} (options checked={g.get('options_checked')}, "
                 f"dropped={g.get('options_dropped')})")
        for w in data.get("warnings", [])[:4]:
            self.log(f"warning: {w}")

    def a_search(self) -> None:
        self.step("A", "Public route search (POST /api/trips/search)")
        self.log(f"{self.ctx['origin']['name']} -> {self.ctx['dest']['name']}, student fare, budget 80 BDT")
        data = self.search("A", self.session, budget_bdt=80, prefer_student_fare=True)
        if data:
            self.print_routes(data)
            self.log(f"{len(data.get('routes') or [])} route option(s) returned (3 when the data supports it).")
            self.ctx["route_before"] = data
            self.results["A"] = "ok"

    def b_chat_route(self) -> None:
        self.step("B", "Chat route query (POST /api/chat)")
        text = CHAT_ROUTE_TEXT
        if self.ctx["origin"]["name"] != PREFERRED_ORIGIN or self.ctx["dest"]["name"] != PREFERRED_DESTINATION:
            text = (f"{self.ctx['origin']['name']} theke {self.ctx['dest']['name']} jete chai, "
                    "budget 80 taka, student fare dao")
        self.log(f"you: {text}")
        status, body = self.api.call("POST", "/api/chat", json={"session_id": self.session, "message": text})
        d = self.data(status, body)
        if not d:
            return self.fail("B", "chat", status, body)
        self.log(f"bot: {d['reply_text']}")
        g = d["grounding"]
        self.log(f"intent={d['intent']} grounding: valid={g['valid']} fallback_used={g.get('fallback_used')} "
                 f"violations={len(g.get('violations', []))}")
        self.results["B"] = "ok"

    def c_chat_draft(self) -> None:
        self.step("C", "Chat report draft (user1, POST /api/chat)")
        hub = self.ctx["incident"]["name"]
        text = f"{hub} e onek pani jome ache, bike chola jachhe na"
        self.log(f"user1: {text}")
        status, body = self.api.call("POST", "/api/chat", token=self.tokens.get("user1"),
                                     json={"session_id": self.session, "message": text})
        d = self.data(status, body)
        if not d:
            return self.fail("C", "chat", status, body)
        self.log(f"bot: {d['reply_text']}")
        draft = (d.get("structured_payload") or {}).get("draft_report")
        if draft:
            self.ctx["draft_id"] = draft["draft_id"]
            self.log(f"draft created: draft_id={draft['draft_id']} type={draft['report_type']} "
                     f"severity={draft['severity']} status={draft['status']} (not submitted yet)")
            self.results["C"] = "ok"
        else:
            self.log("No draft came back from chat (see reply above).")
            self.results["C"] = "no draft"

    def d_submit(self) -> None:
        self.step("D", "Confirm the draft (user1, POST /api/reports/drafts/{id}/submit)")
        if not self.ctx.get("draft_id"):
            self.log("No chat draft available, so creating the report directly with POST /api/reports (same validation).")
            status, body = self.api.call("POST", "/api/reports", token=self.tokens.get("user1"), json={
                "report_type": "waterlogging", "severity": "heavy", "affected_hub": self.ctx["incident"]["hub_id"],
                "description": "Heavy waterlogging reported by user1", "session_id": self.session})
            created = self.data(status, body) or {}
            report = created.get("report", created) if created else None
        else:
            status, body = self.api.call("POST", f"/api/reports/drafts/{self.ctx['draft_id']}/submit",
                                         token=self.tokens.get("user1"))
            report = (self.data(status, body) or {}).get("report")
        if not report:
            return self.fail("D", "submit", status, body)
        self.ctx["report_id"] = report["report_id"]
        self.log(f"report {report['report_id']}: status={report['status']} affects_routing={report['affects_routing']} "
                 f"needs_admin_review={report['needs_admin_review']} flags={report.get('flags')}")
        self.results["D"] = "ok"

    def e_votes(self) -> None:
        self.step("E", "Community corroboration (user2 and user3 vote +1)")
        rid = self.ctx.get("report_id")
        if not rid:
            return self.log("skipped: no report.")
        for who in ("user2", "user3"):
            status, body = self.api.call("POST", f"/api/reports/{rid}/vote", token=self.tokens.get(who), json={"value": 1})
            r = self.data(status, body)
            if r is None:
                self.fail("E", f"{who} vote", status, body)
                continue
            self.log(f"{who} voted +1 -> upvotes={r['upvotes']} community_score={r['community_score']} "
                     f"confidence={r['confidence']} status={r['status']}")
            self.results["E"] = "ok"
            self.pause()

    def f_verify(self) -> None:
        self.step("F", "Admin verification (POST /api/admin/reports/{id}/verify)")
        rid = self.ctx.get("report_id")
        if not rid:
            return self.log("skipped: no report.")
        status, body = self.api.call("POST", f"/api/admin/reports/{rid}/verify", token=self.tokens.get("admin"),
                                     json={"note": "Confirmed by admin during live demo", "extra_time_min": 15})
        d = self.data(status, body)
        if not d:
            return self.fail("F", "verify", status, body)
        r = d["report"]
        self.log(f"report {rid}: status={r['status']} admin_approved={r['admin_approved']} "
                 f"affects_routing={r['affects_routing']} extra_time_min={r['extra_time_min']} edge_closed={r['edge_closed']}")
        if d["sanity"]["flags"]:
            self.log(f"sanity flags: {d['sanity']['flags']}")
        self.results["F"] = "ok"

    def g_recalc(self) -> None:
        self.step("G", "Route recalculation with the verified alert active (POST /api/trips/search)")
        data = self.search("G", self.session, budget_bdt=80, prefer_student_fare=True)
        if not data:
            return
        self.print_routes(data)
        dis = data.get("disruptions") or {}
        for c in dis.get("closed_routes", [])[:5]:
            self.log(f"closed in the route graph: {c['route_id']} ({c['reason']})")
        for dl in dis.get("delayed_routes", [])[:5]:
            self.log(f"delayed by verified alert: {dl['route_id']} +{dl['extra_time_min']} min")
        if not dis.get("closed_routes") and not dis.get("delayed_routes"):
            self.log("no route is affected by the verified alert on this journey")
        before = (self.ctx.get("route_before") or {}).get("routes") or []
        after = data.get("routes") or []
        if before and after:
            self.log(f"top route before: {_mins(before[0].get('total_duration_min'))} -> now: "
                     f"{_mins(after[0].get('total_duration_min'))}")
        hub = self.ctx["incident"]["name"]
        avoided = self.search("G", self.session, avoid_hubs=[hub])
        if avoided is not None:
            n = len(avoided.get("routes") or [])
            self.log(f"avoiding {hub}: {n} route option(s)"
                     + ("" if n else f" ({(avoided.get('no_route_reason') or {}).get('code', 'no route')})"))
        self.results["G"] = "ok"

    def h_simulation(self) -> None:
        self.step("H", "Per-session simulation: heavy rain (user1, POST /api/simulations)")
        o, d, zone = self.ctx["origin"], self.ctx["dest"], self.ctx["incident"]
        status, body = self.api.call("POST", "/api/simulations", token=self.tokens.get("user1"), json={
            "session_id": self.session, "scenario_type": "heavy_rain", "zone_hub": zone["hub_id"],
            "origin": o["name"], "destination": d["name"], "prefer_student_fare": True})
        sim = self.data(status, body)
        if not sim:
            return self.fail("H", "simulation", status, body)
        self.log(f"scenario {sim['scenario_id']} ({sim['scenario_type']}) zone={sim['zone_hub']} "
                 f"fare_scenario={sim['effects'].get('fare_scenario')}")
        if sim["broken_segments"]:
            for seg in sim["broken_segments"][:6]:
                self.log(f"broken segment: {seg['route_id']} - {seg['reason']}")
        else:
            self.log("broken segments: none (the dataset flags no flood-prone segments, so none are invented)")
        c = sim.get("before_after_comparison") or {}
        if c.get("available"):
            self.log(f"before: {c['original_route_ids']} {_mins(c['original_duration_min'])} "
                     f"{c['original_fare_min']}-{c['original_fare_max']} BDT")
            self.log(f"after : {c['new_route_ids']} {_mins(c['new_duration_min'])} "
                     f"{c['new_fare_min']}-{c['new_fare_max']} BDT (rerouted={c['rerouted']})")
        for w in (sim["effects"].get("warnings") or [])[:4]:
            self.log(f"warning: {w}")
        # per-session isolation: a different session must not see this scenario
        other = f"{self.session}_OTHER"
        data = self.search("H", other)
        if data is not None:
            self.log(f"isolation check: session {other} sees active_scenario={data.get('active_scenario')} (must be None)")
        mine = self.search("H", self.session)
        if mine is not None:
            sc = mine.get("active_scenario") or {}
            self.log(f"session {self.session} search now runs under scenario: {sc.get('scenario_type')}")
        self.results["H"] = "ok"

    def i_rideshare(self) -> None:
        self.step("I", "Ride-share board + fare-split bot")
        o, d = self.ctx["origin"], self.ctx["dest"]
        status, body = self.api.call("POST", "/api/rideshare/posts", token=self.tokens.get("user1"), json={
            "mode": "rickshaw", "origin_hub": o["name"], "destination_hub": d["name"], "seats_total": 3,
            "notes": "Demo: sharing a rickshaw"})
        post = self.data(status, body)
        if not post:
            return self.fail("I", "create post", status, body)
        self.log(f"user1 created {post['post_id']} seats {post['seats_taken']}/{post['seats_total']}")
        for who in ("user2", "user3"):
            status, body = self.api.call("POST", f"/api/rideshare/posts/{post['post_id']}/join", token=self.tokens.get(who))
            post = self.data(status, body)
            if not post:
                return self.fail("I", f"{who} join", status, body)
            self.log(f"{who} joined -> seats {post['seats_taken']}/{post['seats_total']} status={post['status']}")
            self.pause()
        if post["per_person_min_bdt"] is None:
            self.log("No fare estimate available for this pair: " + "; ".join(w["message"] for w in post["warnings"]))
        else:
            self.log(f"total estimate {post['estimated_total_fare_min_bdt']}-{post['estimated_total_fare_max_bdt']} BDT "
                     f"split {post['seats_taken']} ways")
            self.log(f"per person: {post['per_person_min_bdt']}-{post['per_person_max_bdt']} BDT; "
                     f"suggested safe amount: {post['suggested_safe_per_person_bdt']} BDT ({post['fare_disclaimer']})")
        self.results["I"] = "ok"

    def j_fake(self) -> None:
        self.step("J", "Grounding: fake AI route is blocked (POST /api/grounding/fake-test)")
        status, body = self.api.call("POST", "/api/grounding/fake-test", headers={"X-Demo-Secret": self.secret}, json={
            "route_ids": ["BUS_MIRPUR_MOTIJHEEL_DIRECT"], "claimed_fare_bdt": 25,
            "hub_ids": [self.ctx["origin"]["hub_id"], self.ctx["dest"]["hub_id"]], "session_id": self.session})
        d = self.data(status, body)
        if d is None:
            self.fail("J", "fake test", status, body)
            if status == 403:
                self.log("   (wrong demo secret: pass --demo-secret or set DEMO_SECRET)")
            return
        self.log(f"grounded={d.get('grounded')} blocked={d.get('blocked')} safe_for_display={d.get('safe_for_display')}")
        if d.get("fallback_action"):
            self.log(f"fallback: {d['fallback_action']}")
        for v in (d.get("violations") or [])[:8]:
            self.log(f"violation {v.get('code')}: {v.get('message')}")
        eng = d.get("engine_result")
        if eng:
            self.log(f"real engine answer instead: {eng['legs']} {eng['total_regular_fare_min_bdt']}-"
                     f"{eng['total_regular_fare_max_bdt']} BDT, {_mins(eng['total_duration_min'])}")
        self.results["J"] = "ok"

    def k_reset(self) -> None:
        self.step("K", "Reset the simulation (user1, POST /api/simulations/reset)")
        status, body = self.api.call("POST", "/api/simulations/reset", token=self.tokens.get("user1"),
                                     json={"session_id": self.session})
        d = self.data(status, body)
        if d is None:
            return self.fail("K", "reset", status, body)
        self.log(f"status={d['status']} (scenario {d.get('scenario_type')}); normal fares and routes are back")
        data = self.search("K", self.session)
        if data is not None:
            self.log(f"search after reset: active_scenario={data.get('active_scenario')}")
        self.results["K"] = "ok"

    def l_events(self) -> None:
        self.step("L", "Event poll (GET /api/events)")
        status, body = self.api.call("GET", "/api/events", params={"since_id": 0, "session_id": self.session, "limit": 100})
        d = self.data(status, body)
        if d is None:
            return self.fail("L", "events", status, body)
        events = d["events"]
        self.log(f"{len(events)} events, latest_id={d['latest_id']}")
        for e in events[-25:]:
            where = f" session={e['session_id']}" if e.get("session_id") else ""
            ref = e.get("report_id") or e.get("route_id") or ""
            self.log(f"#{e['event_id']:<4} {e['event_type']:<24}{where} {ref}".rstrip())
        self.results["L"] = "ok"

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        self.out(f"DhakaPath demo - session {self.session}")
        self.login_all()
        if "user1" not in self.tokens:
            self.out("\nCannot continue without demo logins.")
            return 1
        self.step("*", "Check transit data (GET /api/transit/hubs, /api/transit/routes)")
        if not self.discover():
            return 1
        self.log(f"{len(self.ctx['hubs'])} hubs, {len(self.ctx['edges'])} active route edges")
        for fn in (self.a_search, self.b_chat_route, self.c_chat_draft, self.d_submit, self.e_votes, self.f_verify,
                   self.g_recalc, self.h_simulation, self.i_rideshare, self.j_fake, self.k_reset, self.l_events):
            self.pause()
            try:
                fn()
            except (KeyError, TypeError, IndexError) as exc:  # unexpected response shape: report, keep going
                self.log(f"!! unexpected response shape in {fn.__name__}: {exc!r}")
                self.results[fn.__name__[0].upper()] = "FAILED"
        self.out("\n=== Summary " + "=" * 50)
        for k, v in self.results.items():
            self.out(f"  {k}: {v}")
        return 0 if all(v == "ok" for v in self.results.values()) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DhakaPath live demo (real HTTP calls only).")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--session", default="DEMO_SESSION")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between steps")
    ap.add_argument("--demo-secret", default=os.getenv("DEMO_SECRET", "change_me_debug_secret"))
    ap.add_argument("--password", default="password123", help="password of the seeded demo accounts")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with httpx.Client(timeout=30) as client:
        demo = Demo(Api(client, args.base_url), args.session, args.interval, args.demo_secret, args.password)
        try:
            return demo.run()
        except BackendUnreachable as exc:
            print(f"\nCannot reach the backend at {args.base_url}: {exc}\n"
                  "Start it with: uvicorn app.main:app --reload")
            return 2


if __name__ == "__main__":
    sys.exit(main())
