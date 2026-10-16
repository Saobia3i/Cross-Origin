"""Natural-language understanding for the chat assistant (English + Banglish).

`parse_message` tries the LLM (llm_service.extract_intent, validated JSON) and falls back to the rule-based parser,
which works offline and is what the tests exercise. Everything extracted is TEXT the user said: hub names are only
resolved against the database by chat_service (or by a HubIndex when one is passed in), never trusted from the LLM.

`parse_query` is the hook used by POST /api/trips/search for `query_text`.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session, select

from app.enums import TransitMode, values
from app.models import Hub
from app.schemas import ChatEntities, IntentResult
from app.services.hub_alias import compact_key

BANGLA_CHARS = re.compile(r"[ঀ-৿]")
BANGLISH_MARKERS = {
    "theke", "thekay", "jabo", "jete", "chai", "koro", "dao", "taka", "takar", "koto", "lagbe", "ki", "ache", "jome",
    "joma", "hobe", "moddhe", "ami", "amar", "onek", "pani", "dekho", "dekhao", "bhai", "kemon", "korbo", "kothay",
    "hoy", "na", "ha", "tk", "ta", "ekhon", "jam", "hisebe", "shuru", "bondho", "dhonnobad", "assalamu", "salam"}

# -------------------------------------------------------------------------- normalization
_REPLACEMENTS = [
    (r"৳", " taka "),
    (r"\b(?:takar|taka|tk|bdt|tk\.)\b", "taka"),
    (r"\b(?:jete chai|jete chay|jaite chai|jaite chay|jete cai|jabo|jabe|jaite|jawa|jai|jacchi|jachchi)\b", "jabo"),
    (r"\b(?:thekay|thake|theke)\b", "theke"),
    (r"\b(?:dekhao|dekhan|dekhun|dekho)\b", "dekho"),
    (r"\b(?:korun|koren|kor|koro)\b", "koro"),
    (r"\bpani\s+(?:joma|jome|jomche|jomeche|jomse|jomechhe|jomey|jomlo)\b", "pani joma"),
    (r"\b(?:jolabodhdho|jalabaddho|waterlog(?:ged|ging)?|flood(?:ed|ing)?)\b", "pani joma"),
    (r"\b(?:chatro|chhatro|shikkharthi|students?)\b", "student"),
    (r"\b(?:shosta|sasta|shasta|cheapest|cheaper|cheap)\b", "cheap"),
    (r"\b(?:druto|taratari|quickest|fastest|faster|quick)\b", "fastest"),
    (r"\b(?:brishti|bristi|borsha|heavy rain|raining|rainy|rain)\b", "rain"),
]
_FILLER = {"ta", "ti", "ke", "please", "plz", "pls"}
_TERMINATORS = {"jabo", "go", "going", "budget", "taka", "student", "avoid", "koro", "fastest", "cheap", "route", "dao",
                "e", "te", "with", "under", "within", "by", "via", "chai", "hisebe", "moddhe", "dekho", "fare", "and",
                "also", "ar", "tarpor", "then", "but", "kintu", "ei", "shurur"}
_LEADING_NOISE = {"ami", "amar", "i", "want", "need", "to", "go", "route", "from", "show", "find", "please", "plz", "hey",
                  "ekhon", "now", "the", "a", "me", "give", "tell", "dao", "dekho", "koro", "thik", "ache"}


# -------------------------------------------------------------------------- Bangla script -> canonical Banglish
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
# Phrase -> canonical token(s). Longest phrases are tried first; most match as a word prefix so suffixes ("-e", "-te",
# "-r") are absorbed. Entries in _BN_EXACT must be a whole word (short words that are prefixes of many others).
_BN_PHRASES = {
    # hubs
    "মিরপুর": "mirpur", "মতিঝিল": "motijheel", "ফার্মগেট": "farmgate", "উত্তরা": "uttara north", "শাহবাগ": "shahbagh",
    "ধানমন্ডি": "dhanmondi", "মোহাম্মদপুর": "mohammadpur", "আগারগাঁও": "agargaon", "মহাখালী": "mohakhali",
    "বাড্ডা": "badda", "যাত্রাবাড়ী": "jatrabari", "বনশ্রী": "banasree", "মালিবাগ": "malibagh",
    "সায়েন্স ল্যাব": "science lab", "কারওয়ান বাজার": "karwan bazar", "গাবতলী": "gabtoli", "পল্লবী": "pallabi",
    "কাজীপাড়া": "kazipara", "শেওড়াপাড়া": "shewra para", "নিউ মার্কেট": "new market", "বিজয় সরণি": "bijoy sarani",
    "ক্যান্টনমেন্ট": "cantonment", "ঢাকা বিশ্ববিদ্যালয়": "dhaka univ.", "সচিবালয়": "secretariat",
    "স্টেডিয়াম": "stadium", "খিলক্ষেত": "khilkhet", "কুড়িল": "kuril", "সোনারগাঁও": "sonargaon hotel",
    "শেরাটন": "sheraton", "বাংলা মোটর": "bangla motor", "কাকরাইল": "kakrail", "আজিমপুর": "azampur",
    "কলাবাগান": "kalabagan", "তালতলা": "taltola", "শ্যামলী": "shamoli", "রাজলক্ষ্মী": "rajlokkhi",
    "আসাদ গেট": "asad gate", "দারুস সালাম": "darussalam", "জাহাঙ্গীর গেট": "jahangir gate", "ইউনিভার্সিটি": "dhaka univ.",
    # journey / budget
    "যেতে চাই": " jabo ", "যেতে চাচ্ছি": " jabo ", "যেতে": " jabo ", "যাবো": " jabo ", "যাব": " jabo ",
    "যাচ্ছি": " jabo ", "থেকে": " theke ", "বাজেট": " budget ", "টাকা": " taka ", "৳": " taka ",
    "সবচেয়ে দ্রুত": " fastest ", "দ্রুত": " fastest ", "সবচেয়ে সস্তা": " cheap ", "সস্তা": " cheap ",
    "কম খরচ": " cheap ", "কম পরিবর্তন": " fewest transfer ", "ভাড়া কত": " koto fare ", "কত লাগবে": " koto lagbe ",
    "কত": " koto ", "ভাড়া": " fare ", "ছাত্র": " student ", "স্টুডেন্ট": " student ", "শিক্ষার্থী": " student ",
    "রুট": " route ", "রিকশা": " rickshaw ", "মেট্রো": " metro ", "সিএনজি": " cng ", "লেগুনা": " leguna ",
    "বাস": " bus ", "বিকল্প": " alternative route ", "অন্য রুট": " alternative route ", "এড়িয়ে": " avoid ",
    "বাদ দিয়ে": " avoid ", "এড়াতে": " avoid ",
    # street conditions / reports
    "জলাবদ্ধতা": " pani joma ", "পানি জমে": " pani joma ", "পানি জমা": " pani joma ", "জ্যাম": " jam ",
    "যানজট": " jam ", "ট্রাফিক": " traffic ", "দুর্ঘটনা": " accident ", "অ্যাক্সিডেন্ট": " accident ",
    "নির্মাণ": " construction ", "রাস্তা খোঁড়া": " construction ", "বন্ধ": " bondho ", "অবরোধ": " blockade ",
    "রিপোর্ট": " report ", "রাস্তা পরিষ্কার": " road clear ", "জ্যাম নেই": " jam nai ", "বৃষ্টি": " rain ",
    "আছে": " ache ", "হয়েছে": " hoyeche ", "খুব": " khub ", "অনেক": " onek ", "একটু": " ektu ",
    # misc
    "নিয়ম": " niyom ", "ছাড়": " discount ", "নির্ভরযোগ্য": " biswas ", "বিশ্বাস": " biswas ", "সিমুলেশন": " simulation ",
    "অফিস টাইম": " peak ", "অফিস": " office ", "ছুটি": " holiday ", "কনফার্ম": " confirm ", "জমা দিন": " submit ",
    "বাতিল": " cancel ", "ধন্যবাদ": " dhonnobad ", "হ্যালো": " hello ", "আসসালামু": " salam ", "সালাম": " salam ",
}
_BN_EXACT = {"না": " na ", "হ্যাঁ": " ha ", "হ্যা": " ha ", "জি": " ji ", "ঠিক": " thik ", "কি": " ki ", "কী": " ki ",
             "এ": " e ", "ও": " ar ", "আমি": " ami ", "আমার": " amar "}
_BN_ANY = "ঀ-৿"
_BN_PHRASE_RE = re.compile(
    r"(?<![" + _BN_ANY + r"])(" + "|".join(re.escape(k) for k in sorted(_BN_PHRASES, key=len, reverse=True)) + r")")
_BN_EXACT_RE = re.compile(
    r"(?<![" + _BN_ANY + r"])(" + "|".join(re.escape(k) for k in sorted(_BN_EXACT, key=len, reverse=True))
    + r")(?![" + _BN_ANY + r"])")


_BN_HUB_KEYS = [k for k, v in _BN_PHRASES.items() if not v.startswith(" ")]
_BN_AVOID_RE = re.compile(
    r"((?:" + "|".join(re.escape(k) for k in sorted(_BN_HUB_KEYS, key=len, reverse=True))
    + r")[" + _BN_ANY + r"]*(?:\s*[0-9]+)?)\s+(?:এড়িয়ে|বাদ দিয়ে|এড়াতে|বাদ)(?![" + _BN_ANY + r"])")


def bangla_to_banglish(text: str) -> str:
    """Rewrite Bangla-script text into the canonical Banglish tokens the rule parser understands (digits, keywords, hub
    names). Unknown Bangla words are dropped. Text without Bangla characters is returned unchanged."""
    if not BANGLA_CHARS.search(text or ""):
        return text
    t = text.translate(_BN_DIGITS)
    t = _BN_AVOID_RE.sub(lambda m: "avoid " + m.group(1), t)  # Bangla verb follows the hub: "ফার্মগেট এড়িয়ে"
    t = _BN_EXACT_RE.sub(lambda m: _BN_EXACT[m.group(1)], t)
    t = _BN_PHRASE_RE.sub(lambda m: " " + _BN_PHRASES[m.group(1)] + " ", t)
    return re.sub(r"[" + _BN_ANY + r"]+", " ", t)


def normalize_banglish(text: str) -> str:
    """Lowercase, map common Banglish spellings to canonical tokens (theke, jabo, taka, koro, pani joma, ...),
    drop punctuation and filler particles. Hub names are left as typed."""
    t = bangla_to_banglish(text or "").lower()
    for pattern, repl in _REPLACEMENTS:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"[^\w\s.%]|_", " ", t)
    tokens = [w.strip(".") for w in t.split()]
    return " ".join(w for w in tokens if w and w not in _FILLER)


def detect_language(text: str) -> str:
    if BANGLA_CHARS.search(text or ""):
        return "bn"
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return "banglish" if len(words & BANGLISH_MARKERS) >= 1 and len(words & BANGLISH_MARKERS) >= max(1, len(words) // 6) else "en"


# -------------------------------------------------------------------------- hub mentions
class HubCandidate(BaseModel):
    hub_id: str
    name: str
    matched_text: str
    start: int  # token positions in the normalized text
    end: int
    exact: bool = True
    score: float = 1.0


class HubIndex:
    """Finds hub mentions (id, canonical name or alias; typo-tolerant for long single words) in free text."""

    def __init__(self, hubs: list[Hub]):
        self.names: dict[str, str] = {h.hub_id: h.name for h in hubs}
        self.keys: dict[str, str] = {}
        for h in hubs:
            for label in [h.hub_id.replace("_", " "), h.name, *(h.aliases_json or [])]:
                k = compact_key(label)
                if len(k) >= 2:
                    self.keys.setdefault(k, h.hub_id)

    @classmethod
    def build(cls, session: Session) -> "HubIndex":
        return cls(list(session.exec(select(Hub)).all()))

    def find(self, text: str) -> list[HubCandidate]:
        tokens = normalize_banglish(text).split()
        found: list[HubCandidate] = []
        used: set[int] = set()
        for size in (4, 3, 2, 1):  # longest mention first ("mirpur 10" before "mirpur")
            for i in range(len(tokens) - size + 1):
                if used & set(range(i, i + size)):
                    continue
                phrase = " ".join(tokens[i:i + size])
                hub = self.keys.get(compact_key(phrase))
                if hub and not (size == 1 and phrase.isdigit()):
                    found.append(HubCandidate(hub_id=hub, name=self.names[hub], matched_text=phrase, start=i, end=i + size))
                    used |= set(range(i, i + size))
        for i, tok in enumerate(tokens):  # typos: "shahbaghe" -> SHAHBAGH
            if i in used or len(tok) < 6 or tok in _TERMINATORS or tok in _LEADING_NOISE:
                continue
            best, score = None, 0.0
            for key, hub in self.keys.items():
                if len(key) >= 5:
                    r = SequenceMatcher(None, compact_key(tok), key).ratio()
                    if r > score:
                        best, score = hub, r
            if best and score >= 0.86:
                found.append(HubCandidate(hub_id=best, name=self.names[best], matched_text=tok, start=i, end=i + 1,
                                          exact=False, score=round(score, 3)))
                used.add(i)
        return sorted(found, key=lambda c: c.start)


def resolve_hub_candidates(text: str, session: Session | None = None, hub_index: HubIndex | None = None) -> list[HubCandidate]:
    """Hub mentions in `text`, in order. Needs the database (a session or a prebuilt HubIndex); [] without one."""
    if hub_index is None and session is not None:
        hub_index = HubIndex.build(session)
    return hub_index.find(text) if hub_index else []


# -------------------------------------------------------------------------- rule-based parser
MODE_WORDS = [(r"\brickshaw\b|\brikshaw\b|\bricksha\b", "rickshaw"), (r"\bcng\b|\bauto\b", "cng"),
              (r"\bmetro\b|\bmrt\b|\brail\b", "mrt"), (r"\bbus\b|\blocal bus\b", "local_bus"),
              (r"\buber car\b", "uber_car"), (r"\buber moto\b|\buber bike\b", "uber_moto"),
              (r"\bpathao bike\b", "pathao_bike"), (r"\bpathao car\b", "pathao_car"), (r"\bpathao\b", "pathao_bike"),
              (r"\bmotorbike\b|\bbike\b", "motorbike"), (r"\btaxi\b|\bcar\b", "car"), (r"\bwalk\b|\bhete\b", "walk"),
              (r"\bleguna\b", "leguna")]
REPORT_PATTERNS = [
    ("waterlogging", r"pani joma|waterlog"),
    ("accident", r"\baccident\b|durghotona|collision|crash"),
    ("road_construction", r"construction|road ?work|\bkaj cholche\b|\bkhura\b|digging"),
    ("intersection_blockade", r"\bblock(?:ed|ade)?\b|barricade|\bbondho\b"),
    ("severe_gridlock", r"\bjam\b|gridlock|\btraffic\b|\bjot\b|standstill"),
    ("all_clear", r"all clear|clear hoye|road clear|\bclear hoise\b|jam nai|pani nai"),
]
SIM_SCENARIOS = [
    ("festive_holiday", r"\beid\b|festive|puja|boishakh|victory day|festival"),
    ("regular_holiday", r"holiday|weekend|chhuti|\bfriday\b"),
    ("peak_rush_hour", r"\bpeak\b|rush|office time|\boffice\b"),
    ("heavy_rain", r"\brain\b"),
    ("blocked_intersection", r"\bblock(?:ed|ade)?\b|barricade"),
]
SEVERITY = [("critical", r"critical|impossible|completely|totally|\bbondho\b|nothing moves"),
            ("heavy", r"onek|\bkhub\b|heavy|severe|bhoyonkor|knee|waist|beshi|badly|very"),
            ("light", r"\bektu\b|\bkisu\b|\bkichu\b|slight|little|light|minor")]


def _title(s: str) -> str:
    return " ".join(w.capitalize() if not w.isupper() else w for w in s.split())


def _take_until_terminator(tokens: list[str]) -> list[str]:
    out = []
    for i, tok in enumerate(tokens):
        if tok in _TERMINATORS:
            break
        if tok.isdigit() and i + 1 < len(tokens) and tokens[i + 1] == "taka":
            break
        out.append(tok)
    return out


def _regex_route(t: str) -> tuple[Optional[str], Optional[str]]:
    for pattern in (r"\bfrom\s+(.+?)\s+to\s+(.+)", r"(.+?)\s+theke\s+(.+)", r"^(.+?)\s+to\s+(.+)"):
        m = re.search(pattern, t)
        if not m:
            continue
        origin_tokens = m.group(1).split()
        while origin_tokens and origin_tokens[0] in _LEADING_NOISE:
            origin_tokens.pop(0)
        origin_tokens = origin_tokens[-3:] if len(origin_tokens) > 3 else origin_tokens
        dest_tokens = _take_until_terminator(m.group(2).split())
        if origin_tokens and dest_tokens:
            return _title(" ".join(origin_tokens)), _title(" ".join(dest_tokens))
    return None, None


def _regex_avoid(t: str) -> list[str]:
    tokens = t.split()
    if "avoid" not in tokens:
        return []
    i = tokens.index("avoid")
    after = [w for w in _take_until_terminator(tokens[i + 1:]) if w not in _LEADING_NOISE]
    if after:
        return [_title(" ".join(after))]
    before: list[str] = []
    for tok in reversed(tokens[:i]):
        if tok in _TERMINATORS or tok in _LEADING_NOISE:
            break
        before.insert(0, tok)
    return [_title(" ".join(before[-3:]))] if before else []


def _regex_hub_before(t: str, keyword_pattern: str) -> Optional[str]:
    m = re.search(keyword_pattern, t)
    tokens = (t[:m.start()] if m else t).split()
    while tokens and tokens[-1] in {"e", "te", "ey", "at", "near", "onek", "khub", "ekhane", "road", "er", "a", "in", "is", "ekhon"}:
        tokens.pop()
    while tokens and tokens[0] in _LEADING_NOISE:
        tokens.pop(0)
    return _title(" ".join(tokens[-3:])) if tokens else None


def rule_based_parse(message: str, context: dict | None = None, hub_index: HubIndex | None = None) -> IntentResult:
    """Offline intent + entity extraction for English/Banglish. With a HubIndex, hub mentions are matched against
    the database; without one, hub names are cut out of the sentence by pattern."""
    context = context or {}
    raw = (message or "").strip()
    t = normalize_banglish(raw)
    tokens = t.split()
    ent = ChatEntities()
    language = detect_language(raw)
    has = lambda pattern: re.search(pattern, t) is not None  # noqa: E731

    # ---- simple entities
    m = re.search(r"\bbudget\s*(?:of|is|hocche|holo)?\s*(\d{1,6})\b", t) or re.search(
        r"\b(\d{1,6})\s*taka\s*(?:moddhe|er moddhe|vitore|er niche|under|max|maximum|ba tar kom)?", t)
    if not m:
        m = re.search(r"taka\s*(\d{1,6})", t)  # "BDT 80", "৳80"
    if not m:
        m = re.search(r"\btaka\s*(\d{1,6})\b", t)  # "BDT 80", "৳80"
    amount = int(m.group(1)) if m else None
    if has(r"\bstudent\b"):
        ent.prefer_student_fare = True
    if has(r"\bfastest\b|sobcheye druto|shortest time"):
        ent.optimize = "fastest"
    elif has(r"\bcheap\b|kom kharoch|kom dam"):
        ent.optimize = "cheapest"
    elif has(r"fewest transfer|less change|kom change|no transfer"):
        ent.optimize = "fewest_transfers"
    for pattern, mode in MODE_WORDS:
        if re.search(pattern, t):
            ent.mode = mode
            break
    avoid_words = has(r"\bavoid\b")
    if has(r"avoid (?:the )?(?:rickshaw|cng|bus|metro|mrt)\b|(?:rickshaw|cng|bus|metro|mrt) avoid"):
        ent.avoid_modes = [ent.mode] if ent.mode else []
        avoid_words = False
    rid = re.search(r"\brpt_[a-z0-9_]+\b", raw, re.I)
    if rid:
        ent.report_id = rid.group(0)

    # ---- hub mentions
    mentions = hub_index.find(raw) if hub_index else []
    avoid_idx = [i for i, w in enumerate(tokens) if w == "avoid"]
    if mentions and avoid_words:
        for c in list(mentions):
            # a hub right before "theke" / after "from" or "to" is the journey's own origin or destination
            is_endpoint = (c.end < len(tokens) and tokens[c.end] in {"theke", "to"}) or (
                c.start > 0 and tokens[c.start - 1] in {"from", "to"})
            if not is_endpoint and any(abs(c.start - i) <= 2 or abs(c.end - 1 - i) <= 2 for i in avoid_idx):
                ent.avoid_hubs.append(c.matched_text if not hub_index else c.name)
                mentions.remove(c)
    elif avoid_words and not mentions:
        ent.avoid_hubs = _regex_avoid(t)
    has_from = has(r"\btheke\b|\bfrom\b")
    if mentions:
        rx_o, rx_d = _regex_route(t) if not avoid_words or has_from else (None, None)
        if len(mentions) >= 2:
            ent.origin, ent.destination = mentions[0].name, mentions[1].name
        elif rx_o and rx_d and compact_key(mentions[0].matched_text) in compact_key(rx_o):
            ent.origin, ent.destination = mentions[0].name, rx_d    # destination is not a known hub: keep what was said
        elif rx_o and rx_d and compact_key(mentions[0].matched_text) in compact_key(rx_d):
            ent.origin, ent.destination = rx_o, mentions[0].name
        elif has_from:
            ent.origin = mentions[0].name
        else:
            ent.destination = mentions[0].name
    elif not avoid_words or has_from:
        ent.origin, ent.destination = _regex_route(t)

    # ---- intent
    short = len(tokens) <= 4
    has_draft = bool(context.get("last_draft_id"))
    confirm_yes = {"confirm", "yes", "ha", "haan", "han", "ji", "ok", "okay", "submit", "sure", "yep", "y", "thik"}
    confirm_no = {"cancel", "no", "na", "nope", "batil", "discard"}
    tok = set(tokens)
    report_type = next((rt for rt, p in REPORT_PATTERNS if has(p)), None)
    sim_words = has(r"simulat|what if|\bjodi\b|\bdhoro\b|scenario")
    price_words = has(r"koto lagbe|kotto|how much|\bcost\b|\bdam\b|\bfare\b|ki lagbe|price|\bbhara\b")
    amount_claim = amount is not None and has(r"chaise|chaitese|chaichhe|nilo|nise|charged|demand|asked|cheyeche|beshi")

    intent, conf = "unknown", 0.3
    if short and tok & (confirm_yes | confirm_no) and not report_type and not has(r"route|fare|reliab"):
        if tok & confirm_no and (has_draft or not tok & confirm_yes):
            intent, ent.confirm, conf = "confirm_report_draft", False, 0.8
        elif tok & confirm_yes:
            intent, ent.confirm, conf = "confirm_report_draft", True, 0.9
    if intent == "unknown" and has(r"\breset\b|simulation (?:off|stop|bondho|clear)|normal koro|stop simulation|clear simulation|simulation bondho"):
        intent, conf = "reset_simulation", 0.85
    elif intent == "unknown" and sim_words and next((s for s, p in SIM_SCENARIOS if has(p)), None):
        intent, conf = "apply_simulation", 0.8
        ent.scenario_type = next(s for s, p in SIM_SCENARIOS if has(p))
        if mentions:
            ent.zone_hub = mentions[0].name
    elif intent == "unknown" and has(r"reliab|\btrust|bishshash|biswas|vorosha|confiden|sotti kina|kotota sotti"):
        intent, conf = "alert_reliability", 0.85
        if mentions:
            ent.hub = mentions[0].name
    elif intent == "unknown" and report_type == "all_clear" or (intent == "unknown" and (
            (report_type and report_type != "all_clear" and not has(r"avoid|route")) or amount_claim)):
        intent, conf = "submit_disruption_report", 0.8
        ent.report_type = "fare_surge" if amount_claim and not report_type else report_type
        if amount_claim and has(r"surge|beshi|chaise|chaitese|nilo|nise"):
            ent.report_type = "fare_surge"
        ent.description = raw[:300]
        ent.severity = next((s for s, p in SEVERITY if has(p)), "moderate")
        if ent.report_type == "fare_surge":
            ent.fare_amount_bdt = float(amount) if amount is not None else None
        ent.hub = (mentions[0].name if mentions else None) or (
            _regex_hub_before(t, r"pani joma|\bblock|\bjam\b|\btraffic|accident|construction|all clear|clear")
            if ent.report_type != "fare_surge" else None)
        if ent.report_type == "fare_surge":
            ent.origin = ent.destination = None
    elif intent == "unknown" and has(r"surge|\brule\b|\bniyom\b|discount|policy|explain|chhar") and not (
            mentions and len(mentions) >= 2):
        intent, conf = "explain_fare_rule", 0.8
    elif intent == "unknown" and has(r"alternativ|alternate|onno route|another route|other route|different route|aro ekta"):
        intent, conf = "request_alternative_route", 0.85
    elif intent == "unknown" and price_words and not (ent.origin and ent.destination):
        intent, conf = "compare_fare", 0.75
    elif intent == "unknown" and (ent.origin or ent.destination or ent.avoid_hubs or ent.optimize or amount is not None
                                  or has(r"\broute\b|\bjabo\b|\bgo\b|\btheke\b|\bfrom\b|\bstudent\b|\bavoid\b|\bbudget\b")):
        if ent.avoid_hubs and context.get("last_origin") and not (ent.origin and ent.destination):
            intent, conf = "request_alternative_route", 0.75
        else:
            intent, conf = "find_route", 0.85 if ent.origin and ent.destination else 0.65
            if has(r"koto lagbe|kotto|how much|\bcost\b|ki lagbe|price"):
                ent.fare_focus = True
    elif intent == "unknown" and has(r"^(?:hi|hello|hey|hola|salam|assalamu|namaskar|thanks|thank you|dhonnobad|shukriya|bye|help|who are you)\b|how are you|kemon acho|ki khobor|tumi ke"):
        intent, conf = "smalltalk", 0.8

    if amount is not None and ent.report_type != "fare_surge":
        ent.budget_bdt = amount
    return IntentResult(intent=intent, entities=ent, language=language, confidence=conf, source="rules", normalized_text=t)


# -------------------------------------------------------------------------- public API
def _merge(llm: IntentResult, rules: IntentResult) -> IntentResult:
    """LLM result, with entities it left empty filled from the rule parser (both are text, validated later)."""
    data = llm.entities.model_dump()
    for key, value in rules.entities.model_dump().items():
        if data.get(key) in (None, [], "") and value not in (None, [], ""):
            data[key] = value
    out = llm.model_copy(update={"entities": ChatEntities.model_validate(data), "normalized_text": rules.normalized_text})
    if out.language not in ("en", "bn", "banglish"):
        out.language = rules.language
    return out


def parse_message(message: str, context: dict | None = None, hub_index: HubIndex | None = None,
                  session: Session | None = None, use_llm: bool = True) -> IntentResult:
    """Intent + entities. LLM first (if a key is configured and it answers with valid JSON), else rules."""
    context = context or {}
    rules = rule_based_parse(message, context, hub_index)
    if use_llm:
        from app.services import llm_service

        if llm_service.get_provider_chain():
            try:
                result = _merge(llm_service.extract_intent(message, context, session), rules)
                result.language = rules.language  # the language of the user's text is a fact we can see ourselves
                return result
            except llm_service.LLMUnavailable:
                pass
    return rules


def parse_query(query_text: str, session_id: str | None = None) -> dict:
    """Hook for POST /api/trips/search `query_text`: structured trip fields from free text (rules only, no database)."""
    parsed = rule_based_parse(query_text, {})
    ent = parsed.entities
    if parsed.intent not in ("find_route", "compare_fare", "request_alternative_route") or not (ent.origin or ent.destination):
        return {"error": {"message": "I could not find a journey (origin and destination) in that text."}}
    fields = {"origin": ent.origin, "destination": ent.destination, "budget_bdt": ent.budget_bdt,
              "prefer_student_fare": ent.prefer_student_fare, "avoid_hubs": ent.avoid_hubs, "avoid_modes": ent.avoid_modes,
              "optimize": ent.optimize}
    return {k: v for k, v in fields.items() if v not in (None, [])}
