"""NLP: rule-based parsing of English / Banglish, hub mentions, and the (mocked) LLM path. No live LLM is used."""
import httpx
import pytest
from pydantic import ValidationError

from app.config import settings
from app.models import LlmCache
from app.schemas import IntentResult
from app.services import llm_service, nlp_service
from app.services.nlp_service import HubIndex, normalize_banglish, parse_message, rule_based_parse


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for name in ("openrouter_api_key", "gemini_api_key", "groq_api_key"):
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")


def parse(text, ctx=None, index=None):
    return rule_based_parse(text, ctx or {}, index)


# ---- the ten required sentences ---------------------------------------------------------------------------
def test_origin_and_destination_banglish():
    r = parse("Mirpur 10 theke Motijheel jete chai")
    assert r.intent == "find_route" and r.entities.origin == "Mirpur 10" and r.entities.destination == "Motijheel"
    assert r.language == "banglish" and r.source == "rules"


def test_budget_extraction():
    r = parse("Budget 80 takar moddhe route dao")
    assert r.intent == "find_route" and r.entities.budget_bdt == 80


def test_student_fare_question():
    r = parse("Student fare hisebe koto lagbe?")
    assert r.intent == "compare_fare" and r.entities.prefer_student_fare is True


def test_avoid_hub_with_and_without_context():
    r = parse("Shahbagh avoid koro")
    assert r.entities.avoid_hubs == ["Shahbagh"] and r.intent == "find_route"
    assert parse("Shahbagh avoid koro", {"last_origin": "MIRPUR_10"}).intent == "request_alternative_route"


def test_waterlogging_report():
    r = parse("Shahbagh e onek pani jome ache")
    e = r.entities
    assert r.intent == "submit_disruption_report" and e.report_type == "waterlogging" and e.hub == "Shahbagh"
    assert e.severity == "heavy" and "pani" in e.description


def test_alternative_route():
    assert parse("Alternative route dao").intent == "request_alternative_route"


def test_fastest():
    r = parse("Fastest ta dekho")
    assert r.intent == "find_route" and r.entities.optimize == "fastest"


def test_rickshaw_surge_question_is_a_rule_question():
    r = parse("Rickshaw fare ki surge hobe?")
    assert r.intent == "explain_fare_rule" and r.entities.mode == "rickshaw"


def test_reliability():
    assert parse("Alert ta koto reliable?").intent == "alert_reliability"


def test_confirm():
    r = parse("Confirm")
    assert r.intent == "confirm_report_draft" and r.entities.confirm is True


# ---- more rule coverage --------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,origin,dest", [
    ("from Uttara to Farmgate", "Uttara", "Farmgate"),
    ("Uttara theke Farmgate jabo", "Uttara", "Farmgate"),
    ("Dhanmondi 27 thekay Gabtoli jete chai budget 50 taka", "Dhanmondi 27", "Gabtoli"),
    ("ami Mirpur theke Motijheel jabo", "Mirpur", "Motijheel"),
])
def test_route_phrases(text, origin, dest):
    r = parse(text)
    assert (r.intent, r.entities.origin, r.entities.destination) == ("find_route", origin, dest)


def test_everything_in_one_sentence():
    r = parse("Mirpur 10 theke Motijheel jabo budget 80 taka student fare avoid Shahbagh fastest")
    e = r.entities
    assert (e.origin, e.destination, e.budget_bdt, e.prefer_student_fare, e.avoid_hubs, e.optimize) == (
        "Mirpur 10", "Motijheel", 80, True, ["Shahbagh"], "fastest")


@pytest.mark.parametrize("text,budget", [("budget 120", 120), ("80 tk er moddhe route", 80), ("50 BDT max route dao", 50), ("route dao BDT 90", 90), ("৳70 route", 70),
                                          ("Mirpur theke Farmgate jabo", None)])
def test_budget_variants(text, budget):
    assert parse(text).entities.budget_bdt == budget


@pytest.mark.parametrize("text,optimize", [("cheapest route dao", "cheapest"), ("cheap ta dekho", "cheapest"),
                                           ("sobcheye druto route", "fastest"), ("fewest transfers please", "fewest_transfers"),
                                           ("Mirpur theke Farmgate jabo", None)])
def test_optimize_words(text, optimize):
    assert parse(text).entities.optimize == optimize


@pytest.mark.parametrize("text,kind", [("Mirpur 10 e onek jam", "severe_gridlock"), ("Farmgate e road block", "intersection_blockade"),
                                       ("Shahbagh e pani joma", "waterlogging"), ("Mohakhali e accident hoyeche", "accident"),
                                       ("Farmgate e road construction cholche", "road_construction"),
                                       ("Shahbagh e road clear hoye gese", "all_clear")])
def test_report_types(text, kind):
    r = parse(text)
    assert r.intent == "submit_disruption_report" and r.entities.report_type == kind


@pytest.mark.parametrize("text,severity", [("Shahbagh e onek pani joma", "heavy"), ("Shahbagh e ektu pani joma", "light"),
                                           ("Shahbagh e pani joma", "moderate"), ("Shahbagh e pani joma, completely bondho", "critical")])
def test_report_severity(text, severity):
    assert parse(text).entities.severity == severity


def test_fare_surge_report_has_mode_and_amount():
    r = parse("rickshaw 500 taka chaise")
    assert r.intent == "submit_disruption_report" and r.entities.report_type == "fare_surge"
    assert r.entities.mode == "rickshaw" and r.entities.fare_amount_bdt == 500 and r.entities.budget_bdt is None


def test_fare_questions():
    assert parse("Mirpur theke Motijheel koto lagbe?").entities.fare_focus is True
    assert parse("fare koto?").intent == "compare_fare"
    assert parse("Mirpur 10 theke Motijheel fare ki lagbe").intent == "find_route"


def test_simulation_phrases():
    r = parse("simulate heavy rain")
    assert (r.intent, r.entities.scenario_type) == ("apply_simulation", "heavy_rain")
    assert parse("brishti hole ki hobe, simulate koro").entities.scenario_type == "heavy_rain"
    assert parse("simulate peak hour").entities.scenario_type == "peak_rush_hour"
    assert parse("simulate eid holiday").entities.scenario_type == "festive_holiday"
    assert parse("simulate block").entities.scenario_type == "blocked_intersection"
    assert parse("reset simulation").intent == "reset_simulation"
    assert parse("simulation bondho koro").intent == "reset_simulation"


def test_smalltalk_and_unknown():
    for text in ("hello", "Hi there", "thanks", "dhonnobad", "assalamu alaikum"):
        assert parse(text).intent == "smalltalk", text
    for text in ("blah blah", "", "qwerty zxcv"):
        assert parse(text).intent == "unknown", text


def test_confirm_and_cancel_need_context_for_ambiguous_words():
    assert parse("ha", {"last_draft_id": "drf_1"}).entities.confirm is True
    assert parse("cancel", {"last_draft_id": "drf_1"}).entities.confirm is False
    assert parse("na", {"last_draft_id": "drf_1"}).entities.confirm is False
    assert parse("Mirpur theke Farmgate jabo").intent == "find_route"  # not hijacked by short-word rules


def test_reliability_with_report_id():
    r = parse("is rpt_ab12cd34 reliable?")
    assert r.intent == "alert_reliability" and r.entities.report_id == "rpt_ab12cd34"


def test_avoid_mode_words():
    r = parse("Mirpur theke Farmgate jabo avoid rickshaw")
    assert r.entities.avoid_modes == ["rickshaw"] and r.entities.avoid_hubs == []


def test_output_is_valid_json_for_the_schema():
    for text in ("Mirpur 10 theke Motijheel jete chai", "Confirm", "blah", "Shahbagh e pani joma"):
        data = parse(text).model_dump(mode="json")
        assert IntentResult.model_validate_json(IntentResult(**data).model_dump_json()).intent == data["intent"]
        assert set(data) >= {"intent", "entities", "language", "confidence", "source"}


def test_intent_must_be_in_the_allowed_list():
    with pytest.raises(ValidationError):
        IntentResult(intent="book_a_flight")


# ---- normalization & language -----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Mirpur 10 THEKE Motijheel Jete Chai", "mirpur 10 theke motijheel jabo"),
    ("80 Tk", "80 taka"), ("80 takar moddhe", "80 taka moddhe"), ("৳80", "taka 80"), ("BDT 80", "taka 80"),
    ("Shahbagh e pani jome ache", "shahbagh e pani joma ache"), ("pani jomeche", "pani joma"),
    ("Avoid kor", "avoid koro"), ("dekhao", "dekho"), ("Fastest ta dekho!", "fastest dekho"),
])
def test_normalize_banglish(raw, expected):
    assert normalize_banglish(raw) == expected


@pytest.mark.parametrize("text,lang", [("Mirpur 10 theke Motijheel jete chai", "banglish"), ("find me a route to Farmgate", "en"),
                                       ("আমি মিরপুর থেকে মতিঝিল যাব", "bn"), ("Alert ta koto reliable?", "banglish")])
def test_language_detection(text, lang):
    assert nlp_service.detect_language(text) == lang


# ---- hub mentions (needs hubs) --------------------------------------------------------------------------------------
@pytest.fixture
def index(seeded):
    from app.models import Hub
    seeded.add_all([Hub(hub_id="SHAHBAGH", name="Shahbagh"), Hub(hub_id="MOTIJHEEL", name="Motijheel"),
                    Hub(hub_id="MIRPUR_1", name="Mirpur 1"), Hub(hub_id="MIRPUR_12", name="Mirpur 12")])
    seeded.commit()
    return HubIndex.build(seeded)


def test_hub_index_resolves_aliases_and_longest_match(index):
    names = lambda t: [(c.hub_id, c.matched_text) for c in index.find(t)]  # noqa: E731
    assert names("Uttara theke Farmgate") == [("UTTARA_NORTH", "uttara"), ("FARMGATE", "farmgate")]
    assert names("Mirpur 10 theke Motijheel") == [("MIRPUR_10", "mirpur 10"), ("MOTIJHEEL", "motijheel")]
    assert names("Mirpur theke Motijheel")[0] == ("MIRPUR_10", "mirpur")        # required alias: Mirpur = Mirpur 10
    assert names("Mirpur 12 theke Farmgate")[0][0] == "MIRPUR_12" and names("Farm gate")[0][0] == "FARMGATE"
    assert names("hello world 10 20") == []


def test_hub_index_tolerates_a_typo_but_not_noise(index):
    got = index.find("Shahbaghe onek jam")
    assert got and got[0].hub_id == "SHAHBAGH" and got[0].exact is False
    assert index.find("Narnia theke Mordor") == []


def test_resolve_hub_candidates_needs_a_database():
    assert nlp_service.resolve_hub_candidates("Mirpur 10 theke Motijheel") == []


def test_resolve_hub_candidates_with_session(seeded):
    got = nlp_service.resolve_hub_candidates("Uttara theke Farmgate", seeded)
    assert [c.hub_id for c in got] == ["UTTARA_NORTH", "FARMGATE"]


def test_rule_parser_uses_the_index_for_hubs(index):
    r = parse("Uttara theke Mirpur jete chai", index=index)
    assert (r.entities.origin, r.entities.destination) == ("Uttara North", "Mirpur 10")
    r = parse("Motijheel jete chai", index=index)
    assert (r.entities.origin, r.entities.destination) == (None, "Motijheel")
    r = parse("Farmgate theke", index=index)
    assert (r.entities.origin, r.entities.destination) == ("Farmgate", None)
    r = parse("Uttara theke Farmgate jabo, Mirpur avoid koro", index=index)
    assert (r.entities.origin, r.entities.destination, r.entities.avoid_hubs) == ("Uttara North", "Farmgate", ["Mirpur 10"])
    r = parse("Shahbagh e onek pani jome ache", index=index)
    assert r.entities.hub == "Shahbagh"
    r = parse("simulate block at Farmgate", index=index)
    assert (r.entities.scenario_type, r.entities.zone_hub) == ("blocked_intersection", "Farmgate")


# ---- parse_query (trip search hook) -----------------------------------------------------------------------------------------
def test_parse_query_returns_structured_trip_fields():
    q = nlp_service.parse_query("Mirpur 10 theke Motijheel jete chai, budget 80 taka, student fare dao", "S")
    assert q == {"origin": "Mirpur 10", "destination": "Motijheel", "budget_bdt": 80, "prefer_student_fare": True}


def test_parse_query_reports_when_there_is_no_journey():
    assert "error" in nlp_service.parse_query("hello")
    assert "error" in nlp_service.parse_query("Shahbagh e pani joma")


def test_trip_search_endpoint_uses_the_nlp_service(client):
    r = client.post("/api/trips/search", json={"query_text": "Uttara theke Farmgate jete chai", "session_id": "S"})
    d = r.json()["data"]
    assert r.status_code == 200 and d["query_echo"]["parsed_from_text"] is True and d["routes"]
    assert d["routes"][0]["legs"][0]["from_hub"] == "UTTARA_NORTH"
    r = client.post("/api/trips/search", json={"query_text": "hello"})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "NLP_PARSE_FAILED"


# ---- LLM service (everything mocked) -------------------------------------------------------------------------------------------
def with_keys(monkeypatch, **keys):
    for name in ("openrouter", "gemini", "groq"):
        monkeypatch.setattr(settings, f"{name}_api_key", keys.get(name, ""))


def openai_style(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def gemini_style(content: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": content}]}}]}


INTENT_JSON = '{"intent": "find_route", "entities": {"origin": "Uttara", "destination": "Farmgate", "budget_bdt": 60}, "language": "en", "confidence": 0.9}'


def test_provider_chain_order_and_missing_keys(monkeypatch):
    assert llm_service.get_provider_chain() == []
    with_keys(monkeypatch, groq="g", openrouter="o", gemini="m")
    assert llm_service.get_provider_chain() == ["openrouter", "gemini", "groq"]
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    assert llm_service.get_provider_chain() == ["gemini", "openrouter", "groq"]
    monkeypatch.setattr(settings, "llm_provider", "groq")
    with_keys(monkeypatch, openrouter="o")
    assert llm_service.get_provider_chain() == ["openrouter"]  # a primary without a key is skipped
    monkeypatch.setattr(settings, "llm_provider", "nonsense")
    assert llm_service.get_provider_chain() == ["openrouter"]


def test_no_key_means_unavailable_not_a_crash():
    with pytest.raises(llm_service.LLMUnavailable):
        llm_service.call_llm_json("hi", llm_service.REWRITE_SCHEMA)
    assert parse_message("Mirpur theke Farmgate jabo", {}).source == "rules"


def test_llm_intent_is_used_validated_and_merged_with_rules(monkeypatch, session):
    with_keys(monkeypatch, openrouter="key")
    calls = []
    monkeypatch.setattr(llm_service, "_http_post", lambda url, headers, body, timeout: calls.append(url) or openai_style(INTENT_JSON))
    r = parse_message("Uttara theke Farmgate jabo student fare", {}, None, session)
    assert r.source == "llm:openrouter" and r.intent == "find_route"
    assert (r.entities.origin, r.entities.destination, r.entities.budget_bdt) == ("Uttara", "Farmgate", 60)
    assert r.entities.prefer_student_fare is True  # the LLM left it empty; the rule parser filled it in
    assert calls and "openrouter.ai" in calls[0]


def test_llm_cannot_smuggle_route_ids_or_fares_into_entities(monkeypatch, session):
    with_keys(monkeypatch, groq="key")
    evil = ('{"intent": "find_route", "entities": {"origin": "Uttara", "destination": "Farmgate", "route_ids": ["FAKE"], '
            '"fare_bdt": 5, "duration_min": 1}, "route_ids": ["X"], "fare": 3}')
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: openai_style(evil))
    r = parse_message("Uttara theke Farmgate jabo", {}, None, session)
    dumped = r.model_dump_json()
    assert r.source == "llm:groq" and "FAKE" not in dumped and "fare_bdt" not in dumped and "route_ids" not in dumped


@pytest.mark.parametrize("failure", [httpx.TimeoutException("slow"), httpx.ConnectError("down"),
                                     httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"),
                                                           response=httpx.Response(429))])
def test_timeouts_and_rate_limits_fall_back_to_rules(monkeypatch, session, failure):
    with_keys(monkeypatch, openrouter="key")

    def boom(*a):
        raise failure

    monkeypatch.setattr(llm_service, "_http_post", boom)
    r = parse_message("Mirpur 10 theke Motijheel jete chai", {}, None, session)
    assert r.source == "rules" and r.intent == "find_route" and r.entities.destination == "Motijheel"


@pytest.mark.parametrize("content", ["not json at all", "[1, 2, 3]", '{"entities": {}}', '{"intent": "book_flight", "entities": {}}',
                                     '{"intent": "find_route", "entities": {"budget_bdt": "lots"}}'])
def test_invalid_llm_output_falls_back_to_rules(monkeypatch, session, content):
    with_keys(monkeypatch, openrouter="key")
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: openai_style(content))
    r = parse_message("Budget 80 takar moddhe route dao", {}, None, session)
    assert r.source == "rules" and r.entities.budget_bdt == 80


def test_provider_fallback_order(monkeypatch, session):
    with_keys(monkeypatch, openrouter="o", gemini="m", groq="g")
    seen = []

    def post(url, headers, body, timeout):
        seen.append(url)
        if "openrouter" in url:
            raise httpx.TimeoutException("slow")
        if "generativelanguage" in url:
            return gemini_style("```json\n" + INTENT_JSON + "\n```")  # code fences are tolerated
        raise AssertionError("groq must not be reached")

    monkeypatch.setattr(llm_service, "_http_post", post)
    r = parse_message("Uttara theke Farmgate", {}, None, session)
    assert r.source == "llm:gemini" and len(seen) == 2 and "generativelanguage" in seen[1]


def test_all_providers_failing_uses_rules(monkeypatch, session):
    with_keys(monkeypatch, openrouter="o", gemini="m", groq="g")
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: (_ for _ in ()).throw(httpx.TimeoutException("x")))
    assert parse_message("Alternative route dao", {}, None, session).source == "rules"


def test_requests_are_low_temperature_strict_json(monkeypatch, session):
    with_keys(monkeypatch, openrouter="key", gemini="m")
    bodies = []
    monkeypatch.setattr(llm_service, "_http_post", lambda url, headers, body, timeout: bodies.append((url, body)) or openai_style(INTENT_JSON))
    llm_service.extract_intent("Uttara theke Farmgate", {}, session)
    assert bodies[0][1]["temperature"] == 0 and bodies[0][1]["response_format"] == {"type": "json_object"}
    monkeypatch.setattr(llm_service, "_http_post", lambda url, headers, body, timeout: bodies.append((url, body)) or gemini_style(INTENT_JSON))
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    llm_service.extract_intent("Mirpur theke Farmgate", {"last_origin": "X"}, session)
    cfg = bodies[-1][1]["generationConfig"]
    assert cfg["temperature"] == 0 and cfg["responseMimeType"] == "application/json"


def test_identical_prompts_are_cached(monkeypatch, session):
    with_keys(monkeypatch, openrouter="key")
    n = []
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: n.append(1) or openai_style(INTENT_JSON))
    first = llm_service.extract_intent("Uttara theke Farmgate", {}, session)
    second = llm_service.extract_intent("Uttara theke Farmgate", {}, session)
    assert len(n) == 1 and first.entities == second.entities and second.source == "llm:openrouter"
    assert session.exec(__import__("sqlmodel").select(LlmCache)).all()[0].provider == "openrouter"
    llm_service.extract_intent("Uttara theke Farmgate", {"last_origin": "A"}, session)  # different context = different key
    assert len(n) == 2


def test_cache_can_be_disabled(monkeypatch, session):
    with_keys(monkeypatch, openrouter="key")
    monkeypatch.setattr(settings, "llm_cache_enabled", False)
    n = []
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: n.append(1) or openai_style(INTENT_JSON))
    llm_service.extract_intent("x", {}, session)
    llm_service.extract_intent("x", {}, session)
    assert len(n) == 2


def test_cache_key_is_stable_and_provider_specific():
    a = llm_service.cache_key("hello", {"a": 1}, "openrouter")
    assert a == llm_service.cache_key("hello", {"a": 1}, "openrouter") and len(a) == 64
    assert a != llm_service.cache_key("hello", {"a": 1}, "gemini") != llm_service.cache_key("hello", {"a": 2}, "gemini")


def test_rewrite_grounded_reply(monkeypatch, session):
    with_keys(monkeypatch, openrouter="key")
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: openai_style('{"reply_text": "  Take the metro, it costs Tk 70.  "}'))
    assert llm_service.rewrite_grounded_reply({"verified_reply": "Metro Tk 70"}, "en", session) == "Take the metro, it costs Tk 70."
    monkeypatch.setattr(llm_service, "_http_post", lambda *a: openai_style('{"reply_text": ""}'))
    with pytest.raises(llm_service.LLMUnavailable):
        llm_service.rewrite_grounded_reply({"verified_reply": "x"}, "en", session)
