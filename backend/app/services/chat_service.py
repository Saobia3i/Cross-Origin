"""Conversational commute assistant: one chat turn = parse -> resolve hubs -> call the engines -> grounded reply.

The reply text is built from engine facts with deterministic templates (English / Banglish / Bangla). An LLM may
(1) extract the intent and (2) rephrase the finished reply, but a rephrasing is used only if it adds no new number,
and every final reply is run through grounding_service. Reports from chat are DRAFTS until the user confirms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel import Session, select

from app.config import settings
from app.enums import EventType, TransitMode, values
from app.models import ChatContext, ChatMessage, ChatSession, FareRule, ModeRate, Report, ScenarioMultiplier, User, utcnow
from app.responses import ApiError
from app.schemas import ChatAction, ChatGrounding, ChatResponse, IntentResult
from app.services import alert_service, event_service, grounding_service, nlp_service, search_cache
from app.services import routing_service as rs
from app.services import simulation_service as sims
from app.services.nlp_service import HubIndex

DEFAULT_MODES_FOR_RULES = ["local_bus", "mrt", "rickshaw", "cng"]
MODE_LABEL = {
    "en": {"mrt": "metro", "local_bus": "bus", "rickshaw": "rickshaw", "cng": "CNG", "walk": "walking", "car": "car",
           "motorbike": "motorbike", "pathao_bike": "Pathao bike", "pathao_car": "Pathao car", "uber_car": "Uber car",
           "uber_moto": "Uber moto", "leguna": "leguna"},
    "bn": {"mrt": "মেট্রো", "local_bus": "বাস", "rickshaw": "রিকশা", "cng": "সিএনজি", "walk": "হেঁটে"},
}
OPTIMIZE_LABEL = {"en": {"balanced": "balanced", "cheapest": "cheapest", "fastest": "fastest", "fewest_transfers": "fewest changes"},
                  "bn": {"balanced": "সুষম", "cheapest": "সবচেয়ে সস্তা", "fastest": "সবচেয়ে দ্রুত", "fewest_transfers": "কম বদল"}}

# ---------------------------------------------------------------------------------------------- templates
# key -> (English, Banglish, Bangla). Numbers in them always come from engine facts, the database, or the user.
T: dict[str, tuple[str, str, str]] = {
    "greet": ("Hi! I can find routes and fares, explain fare rules, take disruption reports, check how reliable an alert is, "
              "and simulate rain or a blocked road. Try: \"Mirpur 10 theke Motijheel jabo\".",
              "Hi! Ami route ar fare khuje dite pari, fare-er niyom bujhiye dite pari, rasta-ghater somossha report nite pari, "
              "alert koto reliable bolte pari, ar brishti ba block hole ki hobe simulate korte pari. Likho: \"Mirpur 10 theke Motijheel jabo\".",
              "হ্যালো! আমি রুট ও ভাড়া খুঁজে দিতে পারি, ভাড়ার নিয়ম বোঝাতে পারি, সমস্যা রিপোর্ট নিতে পারি এবং বৃষ্টি বা রাস্তা বন্ধ হলে কী হবে দেখাতে পারি। "
              "লিখুন: \"Mirpur 10 theke Motijheel jabo\"।"),
    "thanks": ("You're welcome! Ask me for another route anytime.", "Welcome! Aro route lagle bolo.",
               "ধন্যবাদ! আরেকটি রুট লাগলে বলুন।"),
    "unknown": ("Sorry, I did not understand. I can find a route (\"Mirpur 10 theke Motijheel\"), compare fares, explain fare rules, "
                "take a disruption report (\"Shahbagh e pani joma\"), check an alert's reliability, or simulate rain.",
                "Sorry, bujhte parlam na. Ami route khuji (\"Mirpur 10 theke Motijheel\"), fare compare kori, fare niyom bujhai, "
                "somossha report nei (\"Shahbagh e pani joma\"), alert reliable kina bolte pari, ba brishti simulate kori.",
                "দুঃখিত, বুঝতে পারিনি। আমি রুট খুঁজি (\"Mirpur 10 theke Motijheel\"), ভাড়া তুলনা করি, ভাড়ার নিয়ম বোঝাই, "
                "সমস্যা রিপোর্ট নিই, অ্যালার্ট কতটা নির্ভরযোগ্য বলি, বা বৃষ্টি সিমুলেট করি।"),
    "need_journey": ("Where are you starting from and where do you want to go? For example: \"Mirpur 10 theke Motijheel\".",
                     "Kothay theke kothay jabe? Jemon: \"Mirpur 10 theke Motijheel\".",
                     "কোথা থেকে কোথায় যাবেন? যেমন: \"Mirpur 10 theke Motijheel\"।"),
    "need_destination": ("Where do you want to go from {origin}?", "{origin} theke kothay jabe?", "{origin} থেকে কোথায় যাবেন?"),
    "unknown_hub": ("I could not find a place called \"{name}\" in the transit data.{hint}",
                    "\"{name}\" naam-er kono jaiga transit data-te pelam na.{hint}",
                    "\"{name}\" নামে কোনো স্থান ট্রানজিট ডেটায় পাইনি।{hint}"),
    "ambiguous_hub": ("\"{name}\" could mean several places: {options}. Which one?",
                      "\"{name}\" mane ekadhik jaiga hote pare: {options}. Kon-ta?",
                      "\"{name}\" একাধিক স্থান হতে পারে: {options}। কোনটি?"),
    "route_best": ("Best route ({label}): {path}. Fare: {fare}.{student}{duration}{transfers}{extra}",
                   "Best route ({label}): {path}. Fare: {fare}.{student}{duration}{transfers}{extra}",
                   "সেরা রুট ({label}): {path}। ভাড়া: {fare}।{student}{duration}{transfers}{extra}"),
    "student_part": (" Student fare: {fare}.", " Student fare: {fare}.", " শিক্ষার্থী ভাড়া: {fare}।"),
    "duration_part": (" About {minutes} minutes.", " Prai {minutes} minute lagbe.", " প্রায় {minutes} মিনিট।"),
    "duration_unknown": (" Travel time is not in the data.", " Travel time data-te nei.", " ভ্রমণের সময় ডেটায় নেই।"),
    "transfers_part": (" {n} change(s).", " {n} bar change.", " {n} বার বদল।"),
    "more_options": (" {n} other option(s) are in the list.", " Aro {n} ta option list-e ache.", " তালিকায় আরও {n}টি বিকল্প আছে।"),
    "over_budget": (" Note: this is over your budget; the cheapest I found is shown.",
                    " Note: eta budget-er beshi; je sobcheye kom ta pelam seta dekhachchi.",
                    " লক্ষ্য করুন: এটি আপনার বাজেটের বেশি; সবচেয়ে কম যেটি পেয়েছি সেটি দেখাচ্ছি।"),
    "avoiding": (" Avoiding {hubs}.", " {hubs} avoid kora hoyeche.", " {hubs} এড়িয়ে চলা হয়েছে।"),
    "no_route": ("I could not find a route from {origin} to {destination}{why}.",
                 "{origin} theke {destination} jaoar kono route pelam na{why}.",
                 "{origin} থেকে {destination} যাওয়ার কোনো রুট পাইনি{why}।"),
    "no_route_why_constraints": (" with those constraints", " ei shorto-gulo diye", " এই শর্তগুলো দিয়ে"),
    "no_alternative": ("There is no other route between {origin} and {destination} besides the one I showed.",
                       "{origin} ar {destination}-er moddhe ager route chara arekta nei.",
                       "{origin} ও {destination}-এর মধ্যে আগের রুট ছাড়া আর কোনো রুট নেই।"),
    "alternative": ("Here is another option: {path}. Fare: {fare}.{student}{duration}",
                    "Arekta option: {path}. Fare: {fare}.{student}{duration}",
                    "আরেকটি বিকল্প: {path}। ভাড়া: {fare}।{student}{duration}"),
    "fare_breakdown": ("Fare for {path}: regular {regular}; student {student}. {rule}",
                       "{path} er fare: regular {regular}; student {student}. {rule}",
                       "{path}-এর ভাড়া: সাধারণ {regular}; শিক্ষার্থী {student}। {rule}"),
    "student_applies": ("The student discount applies on the bus leg(s).", "Bus leg-e student discount pabe.",
                        "বাস অংশে শিক্ষার্থী ছাড় পাবেন।"),
    "student_none": ("No student discount applies on this route (it is for local buses only).",
                     "Ei route-e student discount nei (shudhu local bus-e).", "এই রুটে শিক্ষার্থী ছাড় নেই (শুধু লোকাল বাসে)।"),
    "mode_unavailable": ("I found no {mode}-only route, so this is the best route overall. ",
                         "Shudhu {mode} diye route pelam na, tai somogro best route dekhachchi. ",
                         "শুধু {mode} দিয়ে রুট পাইনি, তাই সামগ্রিক সেরা রুট দেখাচ্ছি। "),
    "rule_student": ("{mode}: students get {pct}% off, minimum {min}.", "{mode}: student-ra {pct}% chhar pay, minimum {min}.",
                     "{mode}: শিক্ষার্থীরা {pct}% ছাড় পায়, সর্বনিম্ন {min}।"),
    "rule_no_student": ("{mode}: no student discount.", "{mode}: student discount nei.", "{mode}: শিক্ষার্থী ছাড় নেই।"),
    "rule_matrix": ("{mode}: fares come from the official fare matrix and do not change with weather or blockages.",
                    "{mode}: fare official fare matrix theke, ar brishti ba block-e bodlay na.",
                    "{mode}: ভাড়া অফিসিয়াল ফেয়ার ম্যাট্রিক্স থেকে, আবহাওয়া বা বন্ধ রাস্তায় বদলায় না।"),
    "rule_range": ("{mode}: fares are negotiated ranges, so I show a minimum and maximum, never one exact fare.",
                   "{mode}: fare dor-kosha hoy, tai ami minimum-maximum range dekhai, ek-ta exact fare na.",
                   "{mode}: ভাড়া দরদাম হয়, তাই আমি সর্বনিম্ন-সর্বোচ্চ রেঞ্জ দেখাই, একটি নির্দিষ্ট ভাড়া নয়।"),
    "rule_formula": ("Estimate: base {base} plus {per_km} per km, minimum {min}.",
                     "Estimate: base {base} ar proti km-e {per_km}, minimum {min}.", "আনুমানিক: বেস {base} ও প্রতি কিমি {per_km}, সর্বনিম্ন {min}।"),
    "rule_mults": (" Fares rise by about x{rain} in heavy rain and x{peak} at peak hour.",
                   " Brishtite proay x{rain}, peak hour-e x{peak} ta fare barte pare.",
                   " ভারী বৃষ্টিতে প্রায় x{rain}, পিক আওয়ারে x{peak} ভাড়া বাড়তে পারে।"),
    "rule_disclaimer": (" Non-metro prices are estimates for planning, not official tariffs.",
                        " Metro chara baki fare estimate, official tariff na.", " মেট্রো ছাড়া অন্য ভাড়া আনুমানিক, সরকারি ট্যারিফ নয়।"),
    "draft_made": ("I prepared a draft report: {kind} ({severity}) at {hub}{fare}. Nothing is submitted yet. Reply \"confirm\" to send it "
                   "for admin verification, or \"cancel\".{login}",
                   "Ami ekta draft report banalam: {kind} ({severity}) {hub}-te{fare}. Ekhono submit hoyni. \"confirm\" bolle admin verification-er "
                   "jonno jabe, ba \"cancel\".{login}",
                   "আমি একটি খসড়া রিপোর্ট বানিয়েছি: {kind} ({severity}), {hub}{fare}। এখনো জমা হয়নি। অ্যাডমিন যাচাইয়ের জন্য পাঠাতে \"confirm\" লিখুন, না চাইলে \"cancel\"।{login}"),
    "draft_login": (" You need to log in as a verified user to submit it.", " Submit korte verified user hisebe login lagbe.",
                    " জমা দিতে যাচাইকৃত ব্যবহারকারী হিসেবে লগইন করতে হবে।"),
    "draft_fare": (", fare {amount} for {mode}", ", {mode}-e fare {amount}", ", {mode}-এ ভাড়া {amount}"),
    "need_report_hub": ("Which place is this about? For example: \"Shahbagh e pani joma\".",
                        "Kon jaigay somossha? Jemon: \"Shahbagh e pani joma\".", "কোন জায়গায় সমস্যা? যেমন: \"Shahbagh e pani joma\"।"),
    "need_fare_surge": ("For a fare report I need the mode and the amount, e.g. \"rickshaw 500 taka chaise Farmgate theke\".",
                        "Fare report-er jonno mode ar amount lagbe, jemon \"rickshaw 500 taka chaise\".",
                        "ভাড়া রিপোর্টের জন্য যানবাহন ও পরিমাণ দরকার, যেমন \"rickshaw 500 taka chaise\"।"),
    "draft_rejected": ("I cannot prepare that report: {why}", "Ei report banate parlam na: {why}", "এই রিপোর্ট বানাতে পারছি না: {why}"),
    "no_draft": ("There is no report draft waiting for confirmation.", "Confirm korar moto kono draft nei.", "নিশ্চিত করার মতো কোনো খসড়া নেই।"),
    "draft_cancelled": ("Okay, I discarded the draft.", "Thik ache, draft bad dilam.", "ঠিক আছে, খসড়া বাদ দিলাম।"),
    "need_login_submit": ("Please log in as a verified user to submit the report. Your draft is saved.",
                          "Report submit korte verified user hisebe login korun. Draft save ache.",
                          "রিপোর্ট জমা দিতে যাচাইকৃত ব্যবহারকারী হিসেবে লগইন করুন। খসড়া সংরক্ষিত আছে।"),
    "submitted": ("Submitted as {id} (pending). An admin must verify it before it affects routes.{flags}",
                  "{id} hisebe submit hoyeche (pending). Route-e prabhab felar age admin-ke verify korte hobe.{flags}",
                  "{id} হিসেবে জমা হয়েছে (pending)। রুটে প্রভাব ফেলার আগে অ্যাডমিনকে যাচাই করতে হবে।{flags}"),
    "submit_failed": ("I could not submit it: {why}", "Submit korte parlam na: {why}", "জমা দিতে পারিনি: {why}"),
    "flags_note": (" It is flagged for admin review ({flags}).", " Admin review-er jonno flag kora hoyeche ({flags}).",
                   " অ্যাডমিন রিভিউয়ের জন্য ফ্ল্যাগ করা হয়েছে ({flags})।"),
    "need_alert": ("Which alert? Tell me the report id, or submit one first.", "Kon alert? Report id bolo, ba age ekta report koro.",
                   "কোন অ্যালার্ট? রিপোর্ট আইডি বলুন, বা আগে একটি রিপোর্ট করুন।"),
    "reliability": ("Alert {id} ({kind}) is {status}. Confidence {conf} and freshness {fresh}. {reasons}",
                    "Alert {id} ({kind}) ekhon {status}. Confidence {conf}, freshness {fresh}. {reasons}",
                    "অ্যালার্ট {id} ({kind}) এখন {status}। কনফিডেন্স {conf}, ফ্রেশনেস {fresh}। {reasons}"),
    "sim_login": ("Please log in as a verified user to run a simulation.", "Simulation chalate verified user hisebe login korun.",
                  "সিমুলেশন চালাতে যাচাইকৃত ব্যবহারকারী হিসেবে লগইন করুন।"),
    "sim_which": ("Which scenario: heavy rain, peak hour, a blocked intersection, or a holiday?",
                  "Kon scenario: brishti, peak hour, block intersection, na chhuti?", "কোন পরিস্থিতি: ভারী বৃষ্টি, পিক আওয়ার, বন্ধ মোড়, নাকি ছুটি?"),
    "sim_zone": ("Which intersection is blocked? For example: \"simulate block at Farmgate\".",
                 "Kon intersection block? Jemon: \"simulate block at Farmgate\".", "কোন মোড় বন্ধ? যেমন: \"simulate block at Farmgate\"।"),
    "sim_applied": ("Applied {scenario} for this session only.{closed}{impact}", "{scenario} shudhu ei session-e apply hoyeche.{closed}{impact}",
                    "{scenario} শুধু এই সেশনে প্রয়োগ হয়েছে।{closed}{impact}"),
    "sim_closed": (" {n} route(s) are closed in the simulation.", " Simulation-e {n} ta route bondho.", " সিমুলেশনে {n}টি রুট বন্ধ।"),
    "sim_rerouted": (" Your best route changed to {path}: fare {fare}.{duration}", " Apnar best route bodle {path} hoyeche: fare {fare}.{duration}",
                     " আপনার সেরা রুট বদলে {path} হয়েছে: ভাড়া {fare}।{duration}"),
    "sim_same": (" Your best route stays {path}: fare {fare}.{duration}", " Apnar best route ekii thakche, {path}: fare {fare}.{duration}",
                 " আপনার সেরা রুট একই থাকছে, {path}: ভাড়া {fare}।{duration}"),
    "sim_none": (" Your trip has no route under this scenario.", " Ei scenario-te apnar trip-er kono route nei.", " এই পরিস্থিতিতে আপনার ট্রিপের কোনো রুট নেই।"),
    "sim_nojourney": (" Tell me where you are going and I will show the impact.", " Kothay jabe bolo, impact dekhabo.",
                      " কোথায় যাবেন বললে প্রভাব দেখাব।"),
    "sim_reset": ("Simulation reset; routes are back to the live network.", "Simulation reset hoyeche; route ekhon live network-e.",
                  "সিমুলেশন রিসেট হয়েছে; রুট এখন লাইভ নেটওয়ার্কে।"),
    "sim_reset_none": ("No simulation was active for this session.", "Ei session-e kono simulation chalu chilo na.",
                       "এই সেশনে কোনো সিমুলেশন চালু ছিল না।"),
    "grounding_failed": ("I could not verify that answer against the transit data, so I am not showing it. Please try rephrasing.",
                         "Ei uttor transit data-r sathe milate parlam na, tai dekhachchi na. Abar onnobhabe bolo.",
                         "এই উত্তর ট্রানজিট ডেটার সাথে মেলাতে পারিনি, তাই দেখাচ্ছি না। অন্যভাবে বলুন।"),
}
SCENARIO_LABEL = {"heavy_rain": ("heavy rain", "brishti", "ভারী বৃষ্টি"), "peak_rush_hour": ("peak hour", "peak hour", "পিক আওয়ার"),
                  "blocked_intersection": ("a blocked intersection", "block intersection", "বন্ধ মোড়"),
                  "regular_holiday": ("a regular holiday", "chhuti-r din", "সাধারণ ছুটি"), "festive_holiday": ("a festive holiday", "uthshober chhuti", "উৎসবের ছুটি")}
FOLLOWUPS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "find_route": (["Show an alternative route", "Fastest route", "How much is the student fare?"],
                   ["Alternative route dao", "Fastest ta dekho", "Student fare hisebe koto lagbe?"],
                   ["Alternative route dao", "Fastest ta dekho", "Student fare koto lagbe?"]),
    "request_alternative_route": (["Fastest route", "Cheapest route", "How much is the student fare?"],
                                  ["Fastest ta dekho", "Cheapest ta dekho", "Student fare koto lagbe?"], ["Fastest ta dekho", "Cheapest dekho", "Student fare koto lagbe?"]),
    "compare_fare": (["Show an alternative route", "Rickshaw fare rules"], ["Alternative route dao", "Rickshaw fare ki surge hobe?"], ["Alternative route dao", "Rickshaw fare rules"]),
    "submit_disruption_report": (["confirm", "cancel"], ["confirm", "cancel"], ["confirm", "cancel"]),
    "confirm_report_draft": (["How reliable is that alert?", "Find a route"], ["Alert ta koto reliable?", "Route dao"], ["Alert ta koto reliable?", "Route dao"]),
    "explain_fare_rule": (["Find a route", "Student fare rules"], ["Mirpur 10 theke Motijheel jabo", "Student fare niyom ki?"], ["Mirpur 10 theke Motijheel jabo", "Student fare niyom ki?"]),
    "alert_reliability": (["Find a route avoiding it", "Report a problem"], ["Route dao, avoid koro", "Ekta somossha report korbo"], ["Route dao", "Somossha report korbo"]),
    "apply_simulation": (["Reset simulation", "Show an alternative route"], ["Simulation reset koro", "Alternative route dao"], ["Simulation reset koro", "Alternative route dao"]),
    "reset_simulation": (["Find a route", "Simulate heavy rain"], ["Route dao", "Brishti simulate koro"], ["Route dao", "Brishti simulate koro"]),
    "smalltalk": (["Mirpur 10 theke Motijheel jabo", "Explain student fare rules", "Simulate heavy rain"],
                  ["Mirpur 10 theke Motijheel jabo", "Student fare niyom ki?", "Brishti simulate koro"], ["Mirpur 10 theke Motijheel jabo", "Student fare niyom ki?", "Brishti simulate koro"]),
    "unknown": (["Mirpur 10 theke Motijheel jabo", "Shahbagh e pani joma", "Rickshaw fare ki surge hobe?"],
                ["Mirpur 10 theke Motijheel jabo", "Shahbagh e pani joma", "Rickshaw fare ki surge hobe?"], ["Mirpur 10 theke Motijheel jabo", "Shahbagh e pani joma", "Rickshaw fare ki surge hobe?"]),
}
ACTIONS: dict[str, list[tuple[str, str]]] = {
    "find_route": [("show_route_options", "routes"), ("highlight_map_route", "selected_route_id")],
    "request_alternative_route": [("show_route_options", "routes"), ("highlight_map_route", "selected_route_id")],
    "compare_fare": [("show_fare_breakdown", "fare_breakdown")],
    "submit_disruption_report": [("show_draft_report", "draft_report"), ("confirm_draft_report", "draft_report")],
    "confirm_report_draft": [("show_draft_report", "report")],
    "alert_reliability": [("show_reliability", "reliability")],
    "apply_simulation": [("apply_simulation", "simulation"), ("show_route_options", "routes")],
}


def _lang_index(lang: str) -> int:
    return {"en": 0, "banglish": 1, "bn": 2}.get(lang, 0)


def tr(key: str, lang: str, **kw) -> str:
    return T[key][_lang_index(lang)].format(**kw)


def reply_language(detected: str, ui_language: Optional[str]) -> str:
    if ui_language == "bn" or detected == "bn":
        return "bn"
    if detected == "banglish":
        return "banglish"
    return ui_language if ui_language in ("en", "banglish") else detected


def _n(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def fmt_money(lo: Optional[float], hi: Optional[float]) -> str:
    if lo is None or hi is None:
        return "not in the data"
    return f"Tk {_n(lo)}" if abs(hi - lo) < 0.005 else f"Tk {_n(lo)}-{_n(hi)}"


def mode_label(mode: str, lang: str) -> str:
    return MODE_LABEL["bn" if lang == "bn" else "en"].get(mode, mode)


# ---------------------------------------------------------------------------------------------- persistence
def load_context(session: Session, session_id: str) -> dict:
    row = session.get(ChatContext, session_id)
    return dict(row.context_state_json or {}) if row else {}


def save_context(session: Session, session_id: str, context: dict) -> None:
    row = session.get(ChatContext, session_id)
    if row is None:
        row = ChatContext(session_id=session_id)
    row.context_state_json = dict(context)
    row.updated_at = utcnow()
    session.add(row)
    session.flush()


def save_message(session: Session, session_id: str, role: str, content: str, intent: str | None = None,
                 entities: dict | None = None, payload: dict | None = None, grounding: dict | None = None) -> ChatMessage:
    msg = ChatMessage(session_id=session_id, role=role, content=content, intent=intent, entities_json=entities,
                      payload_json=payload, grounding_json=grounding)
    session.add(msg)
    session.flush()
    return msg


def _ensure_session(session: Session, session_id: str, user: User | None) -> ChatSession:
    row = session.get(ChatSession, session_id)
    if row is None:
        row = ChatSession(session_id=session_id, user_id=user.id if user else None)
        session.add(row)
        session.flush()
    elif row.user_id is None and user is not None:
        row.user_id = user.id
        session.add(row)
    return row


# ---------------------------------------------------------------------------------------------- turn plumbing
@dataclass
class Out:
    text: str
    payload: dict = field(default_factory=dict)
    ctx: dict = field(default_factory=dict)
    grounding_payload: dict = field(default_factory=dict)  # route_options / alert_ids / fare_rules / mode_rates
    user_figures: list[float] = field(default_factory=list)
    intent: Optional[str] = None
    rewrite: bool = False
    hub_ids: list[str] = field(default_factory=list)


class Turn:
    """Everything one handler needs."""

    def __init__(self, session: Session, session_id: str, user: User | None, lang: str, ctx: dict, intent: IntentResult,
                 index: HubIndex):
        self.s, self.sid, self.user, self.lang, self.ctx, self.res, self.ent, self.index = (
            session, session_id, user, lang, ctx, intent, intent.entities, index)

    def hub_name(self, hub_id: str | None) -> str:
        return self.index.names.get(hub_id or "", hub_id or "?")

    def resolve(self, name: str | None) -> tuple[Optional[str], Optional[Out]]:
        """(hub_id, None) or (None, clarification Out). Names come from the user / LLM and are never trusted."""
        if not name:
            return None, None
        r = rs.resolve_hub(self.s, name)
        if r.hub is not None:
            return r.hub.hub_id, None
        if r.code == "AMBIGUOUS_HUB":
            options = ", ".join(c["name"] for c in r.candidates[:5])
            return None, Out(tr("ambiguous_hub", self.lang, name=name, options=options),
                             {"clarification": {"type": "ambiguous_hub", "name": name, "candidates": r.candidates}})
        hint = ""
        if r.suggestions:
            hint = " Did you mean " + ", ".join(x["name"] for x in r.suggestions[:3]) + "?"
        return None, Out(tr("unknown_hub", self.lang, name=name, hint=hint),
                         {"clarification": {"type": "unknown_hub", "name": name, "suggestions": r.suggestions}})

    def resolve_many(self, names: list[str]) -> tuple[list[str], Optional[Out]]:
        ids: list[str] = []
        for n in names:
            hid, bad = self.resolve(n)
            if bad:
                return [], bad
            if hid and hid not in ids:
                ids.append(hid)
        return ids, None


def _path_text(turn: Turn, option: dict) -> str:
    legs = option["legs"]
    if not legs:
        return ""
    steps = [turn.hub_name(legs[0]["from_hub"])]
    steps += [f"{turn.hub_name(l['to_hub'])} ({mode_label(l['mode'], turn.lang)})" for l in legs]
    return " → ".join(steps)


def _fare_of(option: dict, student: bool) -> str:
    pre = "student" if student else "regular"
    return fmt_money(option.get(f"total_{pre}_fare_min_bdt"), option.get(f"total_{pre}_fare_max_bdt"))


def _duration_text(turn: Turn, option: dict) -> str:
    d = option.get("total_duration_min")
    return tr("duration_part", turn.lang, minutes=_n(d)) if d is not None else tr("duration_unknown", turn.lang)


def _signature(option: dict) -> list[str]:
    return [l["route_id"] for l in option["legs"]]


def _prefs(turn: Turn) -> dict:
    prefs = dict(turn.ctx.get("last_preferences") or {})
    ent = turn.ent
    if ent.prefer_student_fare is not None:
        prefs["prefer_student_fare"] = ent.prefer_student_fare
    if ent.optimize:
        prefs["optimize"] = ent.optimize
    return prefs


def _journey(turn: Turn) -> tuple[Optional[str], Optional[str], Optional[Out]]:
    """Origin and destination hub ids from the message, else the conversation context."""
    ent = turn.ent
    origin, bad = turn.resolve(ent.origin)
    if bad:
        return None, None, bad
    dest, bad = turn.resolve(ent.destination)
    if bad:
        return None, None, bad
    origin = origin or turn.ctx.get("last_origin")
    dest = dest or turn.ctx.get("last_destination")
    if origin and ent.origin and not ent.destination and not dest:
        return origin, None, Out(tr("need_destination", turn.lang, origin=turn.hub_name(origin)), {"clarification": {"type": "need_destination"}})
    if not (origin and dest):
        return None, None, Out(tr("need_journey", turn.lang), {"clarification": {"type": "need_journey"}})
    return origin, dest, None


def _search(turn: Turn, origin: str, dest: str, *, avoid_hubs: list[str], avoid_modes: list[str], prefs: dict,
            budget: Optional[int]) -> dict:
    result = rs.search_routes(
        turn.s, origin, dest, budget_bdt=budget, prefer_student_fare=bool(prefs.get("prefer_student_fare")),
        avoid_hubs=avoid_hubs, avoid_modes=avoid_modes, optimize=prefs.get("optimize") or "balanced",
        session_id=turn.sid, scenario="normal")
    if not result.get("error"):
        grounding = grounding_service.validate_search_result(turn.s, result, "normal", turn.sid)
        result["_grounding"] = grounding
        search_cache.remember_options(turn.sid, result["routes"])
        search_cache.remember_query(turn.sid, {"origin": origin, "destination": dest,
                                               "prefer_student_fare": bool(prefs.get("prefer_student_fare"))})
    return result


def _route_ctx(turn: Turn, origin: str, dest: str, prefs: dict, budget, avoid_hubs, avoid_modes, selected: Optional[dict]) -> dict:
    ctx = {"last_origin": origin, "last_destination": dest, "last_budget": budget,
           "last_preferences": {**prefs, "avoid_hubs": avoid_hubs, "avoid_modes": avoid_modes}}
    if selected:
        ctx["last_route_id"] = selected["route_option_id"]
        ctx["last_route_signature"] = _signature(selected)
    return ctx


# ---------------------------------------------------------------------------------------------- handlers
def _h_find(turn: Turn, alternative: bool = False) -> Out:
    origin, dest, ask = _journey(turn)
    if ask:
        return ask
    ent, ctx = turn.ent, turn.ctx
    prefs = _prefs(turn)
    budget = ent.budget_bdt if ent.budget_bdt is not None else ctx.get("last_budget")
    new_avoid, bad = turn.resolve_many(ent.avoid_hubs)
    if bad:
        return bad
    old = (ctx.get("last_preferences") or {})
    avoid_hubs = list(dict.fromkeys([*(old.get("avoid_hubs") or []), *new_avoid]))
    avoid_modes = list(dict.fromkeys([*(old.get("avoid_modes") or []),
                                      *[m for m in ent.avoid_modes if m in values(TransitMode)]]))
    result = _search(turn, origin, dest, avoid_hubs=avoid_hubs, avoid_modes=avoid_modes, prefs=prefs, budget=budget)
    if result.get("error"):
        return Out(result["error"]["message"], {"error": result["error"]})
    routes = result["routes"]
    seen = [tuple(x) for x in ctx.get("seen_route_signatures", [])]
    if alternative and not new_avoid:
        routes = [r for r in routes if tuple(_signature(r)) not in seen] or []
    names = {"origin": turn.hub_name(origin), "destination": turn.hub_name(dest)}
    base_payload = {"origin": {"hub_id": origin, "name": names["origin"]}, "destination": {"hub_id": dest, "name": names["destination"]},
                    "warning_codes": result.get("warning_codes", []), "applied_preferences": {
                        **prefs, "budget_bdt": budget, "avoid_hubs": avoid_hubs, "avoid_modes": avoid_modes}}
    if not routes:
        if alternative and result["routes"]:
            return Out(tr("no_alternative", turn.lang, **names), {**base_payload, "routes": [], "selected_route_id": None},
                       ctx=_route_ctx(turn, origin, dest, prefs, budget, avoid_hubs, avoid_modes, None))
        why = tr("no_route_why_constraints", turn.lang) if (avoid_hubs or avoid_modes or budget is not None) else ""
        return Out(tr("no_route", turn.lang, why=why, **names),
                   {**base_payload, "routes": [], "selected_route_id": None, "no_route_reason": result.get("no_route_reason")},
                   ctx=_route_ctx(turn, origin, dest, prefs, budget, avoid_hubs, avoid_modes, None))
    best = routes[0]
    student = bool(prefs.get("prefer_student_fare"))
    label = OPTIMIZE_LABEL["bn" if turn.lang == "bn" else "en"].get(best["rank_label"], best["rank_label"])
    extra = tr("more_options", turn.lang, n=len(routes) - 1) if len(routes) > 1 else ""
    if budget is not None and not best["within_budget"]:
        extra = tr("over_budget", turn.lang) + extra
    if avoid_hubs:
        extra += tr("avoiding", turn.lang, hubs=", ".join(turn.hub_name(h) for h in avoid_hubs))
    key = "alternative" if alternative else "route_best"
    kw = dict(path=_path_text(turn, best), fare=_fare_of(best, False),
              student=tr("student_part", turn.lang, fare=_fare_of(best, True)) if student else "",
              duration=_duration_text(turn, best))
    if not alternative:
        kw.update(label=label, transfers=tr("transfers_part", turn.lang, n=best["transfers"]) if best["transfers"] else "", extra=extra)
    text = tr(key, turn.lang, **kw) + (extra if alternative else "")
    ctx_up = _route_ctx(turn, origin, dest, prefs, budget, avoid_hubs, avoid_modes, best)
    ctx_up["seen_route_signatures"] = [*[list(x) for x in seen], _signature(best)][-10:]
    return Out(text, {**base_payload, "routes": routes, "selected_route_id": best["route_option_id"],
                      "fare_focus": bool(ent.fare_focus)}, ctx=ctx_up, grounding_payload={"route_options": routes},
               rewrite=True, hub_ids=[origin, dest])


def _h_compare_fare(turn: Turn) -> Out:
    origin, dest, ask = _journey(turn)
    if ask:
        return ask
    ent, ctx = turn.ent, turn.ctx
    saved_prefs = _prefs(turn)                       # what the user actually asked for (kept in context)
    prefs = {**saved_prefs, "prefer_student_fare": True}   # this answer always shows both regular and student fares
    old = (ctx.get("last_preferences") or {})
    avoid_hubs, avoid_modes = list(old.get("avoid_hubs") or []), list(old.get("avoid_modes") or [])
    note = ""
    chosen = None
    if ent.mode and ent.mode in values(TransitMode):
        only = [m for m in values(TransitMode) if m not in (ent.mode, "walk")]
        result = _search(turn, origin, dest, avoid_hubs=avoid_hubs, avoid_modes=only, prefs=prefs, budget=None)
        if not result.get("error") and result["routes"]:
            chosen = result["routes"][0]
        else:
            note = tr("mode_unavailable", turn.lang, mode=mode_label(ent.mode, turn.lang))
    if chosen is None:
        result = _search(turn, origin, dest, avoid_hubs=avoid_hubs, avoid_modes=avoid_modes, prefs=prefs, budget=None)
        if result.get("error"):
            return Out(result["error"]["message"], {"error": result["error"]})
        routes = result["routes"]
        sig = ctx.get("last_route_signature")
        chosen = next((r for r in routes if _signature(r) == sig), routes[0] if routes else None)
    if chosen is None:
        return Out(tr("no_route", turn.lang, why="", origin=turn.hub_name(origin), destination=turn.hub_name(dest)), {"routes": []})
    legs = [{"route_id": l["route_id"], "mode": l["mode"], "from_hub": l["from_hub"], "to_hub": l["to_hub"],
             "regular": {"min_bdt": l["regular_fare_min_bdt"], "max_bdt": l["regular_fare_max_bdt"]},
             "student": {"min_bdt": l["student_fare_min_bdt"], "max_bdt": l["student_fare_max_bdt"]},
             "student_discount_applies": l["student_discount_applies"]} for l in chosen["legs"]]
    any_discount = any(l["student_discount_applies"] for l in chosen["legs"])
    breakdown = {"route_option_id": chosen["route_option_id"], "legs": legs,
                 "total_regular": {"min_bdt": chosen["total_regular_fare_min_bdt"], "max_bdt": chosen["total_regular_fare_max_bdt"]},
                 "total_student": {"min_bdt": chosen["total_student_fare_min_bdt"], "max_bdt": chosen["total_student_fare_max_bdt"]},
                 "student_discount_applies_any_leg": any_discount}
    rule = tr("student_applies" if any_discount else "student_none", turn.lang)
    text = note + tr("fare_breakdown", turn.lang, path=_path_text(turn, chosen), regular=_fare_of(chosen, False),
                     student=_fare_of(chosen, True), rule=rule)
    ctx_up = _route_ctx(turn, origin, dest, saved_prefs, ctx.get("last_budget"), avoid_hubs, avoid_modes, chosen)
    return Out(text, {"fare_breakdown": breakdown, "routes": [chosen], "selected_route_id": chosen["route_option_id"]},
               ctx=ctx_up, grounding_payload={"route_options": [chosen]}, rewrite=True, hub_ids=[origin, dest])


def _h_fare_rule(turn: Turn) -> Out:
    modes = [turn.ent.mode] if turn.ent.mode in values(TransitMode) else DEFAULT_MODES_FOR_RULES
    lang = turn.lang
    cards, rate_cards, lines = [], [], []
    for mode in modes:
        rule = turn.s.get(FareRule, mode)
        rate = turn.s.get(ModeRate, mode)
        if rule is None:
            continue
        mults = {m.scenario: m.multiplier for m in turn.s.exec(select(ScenarioMultiplier).where(ScenarioMultiplier.mode == mode)).all()}
        label = mode_label(mode, lang).capitalize() if lang != "bn" else mode_label(mode, lang)
        parts = []
        if rule.student_discount_percent > 0:
            parts.append(tr("rule_student", lang, mode=label, pct=_n(rule.student_discount_percent),
                            min=fmt_money(rule.minimum_student_fare_bdt, rule.minimum_student_fare_bdt)))
        else:
            parts.append(tr("rule_no_student", lang, mode=label))
        if rule.use_official_matrix:
            parts.append(tr("rule_matrix", lang, mode=label))
        elif mode in ("rickshaw", "cng", "car", "motorbike"):
            parts.append(tr("rule_range", lang, mode=label))
        if rate and not rule.use_official_matrix and None not in (rate.base_fare_bdt, rate.per_km_bdt, rate.min_fare_bdt):
            parts.append(tr("rule_formula", lang, base=fmt_money(rate.base_fare_bdt, rate.base_fare_bdt),
                            per_km=fmt_money(rate.per_km_bdt, rate.per_km_bdt), min=fmt_money(rate.min_fare_bdt, rate.min_fare_bdt)))
        if mults.get("rain") and mults.get("peak") and not rule.use_official_matrix:
            parts.append(tr("rule_mults", lang, rain=_n(mults["rain"]), peak=_n(mults["peak"])).strip())
        lines.append(" ".join(parts))
        cards.append({"mode": mode, "student_discount_percent": rule.student_discount_percent,
                      "minimum_student_fare_bdt": rule.minimum_student_fare_bdt, "surge_allowed": rule.surge_allowed,
                      "use_official_matrix": rule.use_official_matrix, "allow_formula_fallback": rule.allow_formula_fallback,
                      "scenario_multipliers": {k: v for k, v in mults.items() if v is not None},
                      "summary": lines[-1]})
        if rate:
            rate_cards.append({"mode": mode, "base_fare_bdt": rate.base_fare_bdt, "per_km_bdt": rate.per_km_bdt,
                               "min_fare_bdt": rate.min_fare_bdt, "average_speed_kmh": rate.average_speed_kmh})
    if not cards:
        return Out(tr("unknown", lang), {"rule_cards": []})
    text = "\n".join(lines) + (tr("rule_disclaimer", lang) if any(not c["use_official_matrix"] for c in cards) else "")
    return Out(text, {"rule_cards": cards}, grounding_payload={"fare_rules": [
        {k: c[k] for k in ("mode", "student_discount_percent", "minimum_student_fare_bdt")} for c in cards], "mode_rates": rate_cards})


def _h_report(turn: Turn) -> Out:
    ent, lang = turn.ent, turn.lang
    kind = ent.report_type or "other"
    hub_id, bad = turn.resolve(ent.hub)
    if bad:
        return bad
    payload: dict[str, Any] = {"report_type": kind, "severity": ent.severity or "moderate",
                               "description": (ent.description or "Reported via chat")[:1000], "session_id": turn.sid}
    if kind == "fare_surge":
        if not ent.mode or ent.fare_amount_bdt is None:
            return Out(tr("need_fare_surge", lang), {"clarification": {"type": "need_fare_surge"}})
        payload.update(mode=ent.mode, fare_amount_bdt=ent.fare_amount_bdt)
    if hub_id is None and kind != "fare_surge":
        return Out(tr("need_report_hub", lang), {"clarification": {"type": "need_report_hub"}})
    if hub_id is None and kind == "fare_surge":
        o, d, _ = _journey(turn)
        if not o:
            return Out(tr("need_report_hub", lang), {"clarification": {"type": "need_report_hub"}})
        hub_id = o
    payload["affected_hub"] = hub_id
    user = turn.user
    sanity = alert_service.run_sanity_checks(turn.s, payload, user)
    if sanity.errors:
        return Out(tr("draft_rejected", lang, why=sanity.errors[0]["message"]), {"clarification": {"type": "report_invalid", "errors": sanity.errors}})
    draft = alert_service.create_draft(turn.s, turn.sid, user.id if user else None, payload)
    verified = bool(user and user.is_verified)
    fare = tr("draft_fare", lang, amount=fmt_money(ent.fare_amount_bdt, ent.fare_amount_bdt), mode=mode_label(ent.mode or "", lang)) if kind == "fare_surge" else ""
    text = tr("draft_made", lang, kind=kind.replace("_", " "), severity=payload["severity"], hub=turn.hub_name(hub_id), fare=fare,
              login="" if verified else tr("draft_login", lang))
    draft_report = {"draft_id": draft.draft_id, "status": draft.status, "report_type": kind, "severity": payload["severity"],
                    "affected_hub": hub_id, "affected_hub_name": turn.hub_name(hub_id), "description": payload["description"],
                    "mode": payload.get("mode"), "fare_amount_bdt": payload.get("fare_amount_bdt"),
                    "confirmation_required": True, "preview_flags": list(sanity.flags), "requires_login": not verified}
    return Out(text, {"draft_report": draft_report, "confirmation_required": True}, ctx={"last_draft_id": draft.draft_id},
               user_figures=[ent.fare_amount_bdt] if ent.fare_amount_bdt is not None else [], hub_ids=[hub_id])


def _h_confirm(turn: Turn) -> Out:
    lang, draft_id, user = turn.lang, turn.ctx.get("last_draft_id"), turn.user
    if not draft_id:
        return Out(tr("no_draft", lang), {"clarification": {"type": "no_draft"}})
    if turn.ent.confirm is False:
        if user and user.is_verified:
            try:
                alert_service.cancel_draft(turn.s, draft_id, user)
            except ApiError:
                pass
        return Out(tr("draft_cancelled", lang), {"draft_cancelled": draft_id}, ctx={"last_draft_id": None})
    if not (user and user.is_verified):
        return Out(tr("need_login_submit", lang), {"requires_login": True, "draft_id": draft_id})
    try:
        report = alert_service.submit_draft(turn.s, draft_id, user)
    except ApiError as exc:
        return Out(tr("submit_failed", lang, why=exc.message), {"error": {"code": exc.code, "message": exc.message}})
    flags = list((report.raw_json or {}).get("sanity_flags") or [])
    summary = {"report_id": report.report_id, "status": report.status, "report_type": report.report_type,
               "severity": report.severity, "affected_hub": report.affected_hub, "needs_admin_review": report.needs_admin_review,
               "flags": flags, "admin_verification_required": True, "affects_routing": report.affects_routing}
    text = tr("submitted", lang, id=report.report_id, flags=tr("flags_note", lang, flags=", ".join(flags)) if flags else "")
    return Out(text, {"report": summary}, ctx={"last_draft_id": None, "last_report_id": report.report_id},
               grounding_payload={"alert_ids": [report.report_id]})


def _h_reliability(turn: Turn) -> Out:
    lang, ent = turn.lang, turn.ent
    report_id = ent.report_id or turn.ctx.get("last_report_id")
    if not report_id and ent.hub:
        hub_id, bad = turn.resolve(ent.hub)
        if bad:
            return bad
        row = turn.s.exec(select(Report).where(Report.affected_hub == hub_id, Report.status.in_(["verified", "pending"]))
                          .order_by(Report.created_at.desc())).first()
        report_id = row.report_id if row else None
    if not report_id:
        return Out(tr("need_alert", lang), {"clarification": {"type": "need_alert"}})
    try:
        rel = grounding_service.build_reliability_payload(turn.s, report_id)
    except ApiError as exc:
        return Out(exc.message, {"error": {"code": exc.code, "message": exc.message}})
    report = turn.s.get(Report, report_id)
    text = tr("reliability", lang, id=report_id, kind=report.report_type.replace("_", " "), status=rel["status"],
              conf=f"{rel['confidence']:.2f}", fresh=f"{rel['freshness']:.2f}", reasons=". ".join(rel["reasons"]) + ".")
    return Out(text, {"reliability": rel}, ctx={"last_report_id": report_id}, grounding_payload={"alert_ids": [report_id]})


def _can_simulate(turn: Turn) -> bool:
    if turn.user and turn.user.is_verified:
        return True
    return bool(settings.chat_demo_simulation and turn.sid.upper().startswith("DEMO"))


def _h_simulate(turn: Turn) -> Out:
    lang, ent = turn.lang, turn.ent
    if not _can_simulate(turn):
        return Out(tr("sim_login", lang), {"requires_login": True})
    if not ent.scenario_type:
        return Out(tr("sim_which", lang), {"clarification": {"type": "need_scenario"}})
    zone_id, bad = turn.resolve(ent.zone_hub or ent.hub)
    if bad:
        return bad
    if ent.scenario_type == "blocked_intersection" and not zone_id:
        return Out(tr("sim_zone", lang), {"clarification": {"type": "need_zone_hub"}})
    ctx = turn.ctx
    try:
        res = sims.apply_simulation(turn.s, turn.sid, ent.scenario_type, zone_id, turn.user.id if turn.user else None,
                                    ctx.get("last_origin"), ctx.get("last_destination"),
                                    (ctx.get("last_preferences") or {}).get("prefer_student_fare"))
    except ApiError as exc:
        return Out(exc.message, {"error": {"code": exc.code, "message": exc.message}})
    cmp = res.before_after_comparison or {}
    routes = res.recalculated_routes
    label = SCENARIO_LABEL[ent.scenario_type][_lang_index(lang)]
    n_closed = len(res.effects.get("closed_route_ids", []))
    closed = tr("sim_closed", lang, n=n_closed) if n_closed else ""
    if not cmp.get("available"):
        impact = tr("sim_nojourney", lang)
    elif not routes:
        impact = tr("sim_none", lang)
    else:
        best = routes[0]
        key = "sim_rerouted" if cmp.get("rerouted") else "sim_same"
        impact = tr(key, lang, path=_path_text(turn, best), fare=_fare_of(best, bool((ctx.get("last_preferences") or {}).get("prefer_student_fare"))),
                    duration=_duration_text(turn, best))
    payload = {"simulation": res.model_dump(mode="json"), "routes": routes[:3],
               "selected_route_id": routes[0]["route_option_id"] if routes else None}
    return Out(tr("sim_applied", lang, scenario=label, closed=closed, impact=impact), payload,
               ctx={"last_scenario": ent.scenario_type, "last_zone_hub": zone_id},
               grounding_payload={"route_options": routes[:3]}, hub_ids=[zone_id] if zone_id else [])


def _h_reset(turn: Turn) -> Out:
    if not _can_simulate(turn):
        return Out(tr("sim_login", turn.lang), {"requires_login": True})
    res = sims.reset_simulation(turn.s, turn.sid, turn.user.id if turn.user else None)
    key = "sim_reset" if res.status == "reset" else "sim_reset_none"
    return Out(tr(key, turn.lang), {"simulation": res.model_dump(mode="json")}, ctx={"last_scenario": None, "last_zone_hub": None})


def _h_smalltalk(turn: Turn) -> Out:
    thanks = re.search(r"thank|dhonnobad|shukriya", turn.res.normalized_text)
    return Out(tr("thanks" if thanks else "greet", turn.lang), {})


def _h_unknown(turn: Turn) -> Out:
    return Out(tr("unknown", turn.lang), {"clarification": {"type": "unsupported"}})


HANDLERS = {
    "find_route": _h_find, "request_alternative_route": lambda t: _h_find(t, alternative=True), "compare_fare": _h_compare_fare,
    "submit_disruption_report": _h_report, "confirm_report_draft": _h_confirm, "explain_fare_rule": _h_fare_rule,
    "alert_reliability": _h_reliability, "apply_simulation": _h_simulate, "reset_simulation": _h_reset,
    "smalltalk": _h_smalltalk, "unknown": _h_unknown,
}


# ---------------------------------------------------------------------------------------------- the turn
def _numbers(text: str) -> set[str]:
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", text)}


def _maybe_rewrite(session: Session, out: Out, lang: str) -> str:
    """LLM phrasing of an already-grounded reply; used only if it introduces no number the template lacks."""
    if not (settings.chat_llm_rewrite and out.rewrite):
        return out.text
    from app.services import llm_service

    if not llm_service.get_provider_chain():
        return out.text
    try:
        candidate = llm_service.rewrite_grounded_reply({"verified_reply": out.text}, lang, session)
    except llm_service.LLMUnavailable:
        return out.text
    return candidate if _numbers(candidate) <= _numbers(out.text) else out.text


def _ground(session: Session, session_id: str, out: Out, text: str) -> grounding_service.GroundingResult:
    entities = {"hub_ids": out.hub_ids} if out.hub_ids else {}
    response = {"reply_text": text, "payload": out.grounding_payload, "entities": entities}
    return grounding_service.validate_chat_payload(session, response, None, session_id, user_figures=out.user_figures)


def handle_chat(session: Session, session_id: str, message: str, user: User | None = None,
                ui_language: str | None = None) -> ChatResponse:
    _ensure_session(session, session_id, user)
    ctx = load_context(session, session_id)
    index = HubIndex.build(session)
    intent = nlp_service.parse_message(message, ctx, index, session)
    lang = reply_language(intent.language, ui_language)
    turn = Turn(session, session_id, user, lang, ctx, intent, index)
    user_msg = save_message(session, session_id, "user", message, intent.intent, intent.entities.model_dump(exclude_none=True))

    out = HANDLERS.get(intent.intent, _h_unknown)(turn)
    out.intent = intent.intent

    text = _maybe_rewrite(session, out, lang)
    result = _ground(session, session_id, out, text)
    violations, fallback_used, valid = [], False, True
    if not result.grounded and text != out.text:     # the LLM rephrasing was the problem: use the deterministic template
        violations, fallback_used = [v.model_dump() for v in result.violations], True
        text = out.text
        result = _ground(session, session_id, out, text)
    if not result.grounded:                           # the engine facts themselves do not verify: withhold them
        violations = violations or [v.model_dump() for v in result.violations]
        fallback_used, valid = True, False
        text = tr("grounding_failed", lang)
        out.payload, out.grounding_payload, out.ctx = {"clarification": {"type": "grounding_failed"}}, {}, {}
    grounding = ChatGrounding(checked=True, valid=valid, violations=violations, fallback_used=fallback_used)

    new_ctx = {**ctx, **{k: v for k, v in out.ctx.items()}, "last_intent": intent.intent, "language": lang}
    save_context(session, session_id, new_ctx)
    entities = intent.entities.model_dump(exclude_none=True)
    msg = save_message(session, session_id, "assistant", text, intent.intent, entities, out.payload, grounding.model_dump())
    event_service.publish_event(session, EventType.chat_message, {"intent": intent.intent, "message_id": f"msg_{msg.id}",
                                                                  "grounded": grounding.valid}, session_id=session_id)
    session.commit()

    actions = [] if out.payload.get("clarification") or out.payload.get("error") else [
        ChatAction(type=t, payload_key=k) for t, k in ACTIONS.get(intent.intent, []) if k in out.payload]
    followups = list(FOLLOWUPS.get(intent.intent, FOLLOWUPS["unknown"])[_lang_index(lang)])
    return ChatResponse(
        session_id=session_id, message_id=f"msg_{msg.id}", reply_text=text, language_detected=intent.language,
        intent=intent.intent, entities=entities, actions=actions, structured_payload=out.payload, grounding=grounding,
        context_state=new_ctx, suggested_followups=followups)
