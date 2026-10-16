"""LLM access for the chat assistant: intent/entity extraction and optional natural rewrites.

The LLM never produces routes, fares, hubs, alerts or durations that are used as facts. Extraction output is
validated with Pydantic and later resolved against the database; rewrites are discarded by chat_service unless
they add no new number. Everything has a rule-based fallback, so the app works with no API key at all.

Providers (HTTP via httpx, strict JSON, temperature 0): OpenRouter, Gemini, Groq. Order: LLM_PROVIDER first, then
OpenRouter -> Gemini -> Groq; only providers with a key are tried. Timeouts, rate limits (429), HTTP errors and
invalid JSON move on to the next provider; if all fail LLMUnavailable is raised and callers fall back to rules.
Identical prompts are cached in the llm_cache table (per provider).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError
from sqlmodel import Session, select

from app.config import settings
from app.models import LlmCache
from app.schemas import IntentResult

log = logging.getLogger("dhakapath.llm")
PROVIDER_ORDER = ("openrouter", "gemini", "groq")
INTENTS = ["find_route", "request_alternative_route", "compare_fare", "submit_disruption_report",
           "confirm_report_draft", "explain_fare_rule", "alert_reliability", "apply_simulation",
           "reset_simulation", "smalltalk", "unknown"]

INTENT_SCHEMA = {
    "type": "object", "required": ["intent", "entities"],
    "properties": {
        "intent": {"enum": INTENTS},
        "language": {"enum": ["en", "bn", "banglish"]},
        "confidence": {"type": "number"},
        "entities": {"type": "object", "properties": {
            "origin": "string|null", "destination": "string|null", "budget_bdt": "integer|null",
            "prefer_student_fare": "boolean|null", "avoid_hubs": "string[]", "avoid_modes": "string[]",
            "optimize": "cheapest|fastest|fewest_transfers|balanced|null", "mode": "string|null",
            "report_type": "waterlogging|road_construction|severe_gridlock|intersection_blockade|fare_surge|accident|all_clear|other|null",
            "severity": "light|moderate|heavy|critical|null", "hub": "string|null", "fare_amount_bdt": "number|null",
            "scenario_type": "heavy_rain|peak_rush_hour|blocked_intersection|regular_holiday|festive_holiday|null",
            "zone_hub": "string|null", "report_id": "string|null", "confirm": "boolean|null"}},
    },
}
REWRITE_SCHEMA = {"type": "object", "required": ["reply_text"], "properties": {"reply_text": "string"}}


class LLMUnavailable(Exception):
    """No provider configured, or every provider failed."""


def _key_for(provider: str) -> str:
    return {"openrouter": settings.openrouter_api_key, "gemini": settings.gemini_api_key,
            "groq": settings.groq_api_key}[provider]


def get_provider_chain() -> list[str]:
    """Providers to try, in order: LLM_PROVIDER first, then OpenRouter -> Gemini -> Groq; keyless ones are skipped."""
    primary = (settings.llm_provider or "").strip().lower()
    order = ([primary] if primary in PROVIDER_ORDER else []) + [p for p in PROVIDER_ORDER if p != primary]
    return [p for p in order if _key_for(p)]


def cache_key(message: str, context_summary: Any, provider: str) -> str:
    blob = json.dumps({"m": message, "c": context_summary, "p": provider}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- transport (patched in tests)
def _http_post(url: str, headers: dict, json_body: dict, timeout: float) -> dict:
    response = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _parse_json_text(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    return data


def _call_provider(provider: str, system: str, user: str) -> dict:
    timeout = settings.llm_timeout_s
    if provider in ("openrouter", "groq"):
        url = ("https://openrouter.ai/api/v1/chat/completions" if provider == "openrouter"
               else "https://api.groq.com/openai/v1/chat/completions")
        model = settings.openrouter_model if provider == "openrouter" else settings.groq_model
        body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        data = _http_post(url, {"Authorization": f"Bearer {_key_for(provider)}"}, body, timeout)
        return _parse_json_text(data["choices"][0]["message"]["content"])
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
           f"?key={_key_for('gemini')}")
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    data = _http_post(url, {}, body, timeout)
    return _parse_json_text(data["candidates"][0]["content"]["parts"][0]["text"])


def _meets_schema(data: dict, schema: dict) -> bool:
    return all(k in data for k in schema.get("required", []))


def call_llm_json(prompt: str, schema: dict, *, session: Session | None = None, message: str | None = None,
                  context_summary: Any = None, system: str | None = None) -> dict:
    """Ask the provider chain for a JSON object matching `schema`'s required keys. Raises LLMUnavailable.
    `message` / `context_summary` form the cache key (default: the prompt itself); the cache needs a `session`."""
    chain = get_provider_chain()
    if not chain:
        raise LLMUnavailable("no LLM API key is configured")
    system = system or ("You return ONLY one JSON object, no prose. Schema: " + json.dumps(schema))
    errors: list[str] = []
    for provider in chain:
        key = cache_key(message if message is not None else prompt, context_summary, provider)
        if session is not None and settings.llm_cache_enabled:
            hit = session.exec(select(LlmCache).where(LlmCache.prompt_hash == key)).first()
            if hit is not None and _meets_schema(hit.response_json, schema):
                return {**hit.response_json, "_provider": hit.provider}
        try:
            data = _call_provider(provider, system, prompt)
            if not _meets_schema(data, schema):
                raise ValueError("missing required keys")
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:  # timeout, 429, bad JSON...
            log.warning("LLM provider %s failed: %s", provider, exc)
            errors.append(f"{provider}: {type(exc).__name__}")
            continue
        if session is not None and settings.llm_cache_enabled:
            model = getattr(settings, f"{provider}_model")
            session.add(LlmCache(prompt_hash=key, provider=provider, model=model, response_json=data))
            try:
                session.commit()
            except Exception:  # a concurrent identical request cached it first
                session.rollback()
        data["_provider"] = provider
        return data
    raise LLMUnavailable("; ".join(errors))


# ---------------------------------------------------------------------------- tasks
def _context_summary(context: dict) -> dict:
    keys = ("last_origin", "last_destination", "last_budget", "last_preferences", "last_draft_id", "last_report_id")
    return {k: context.get(k) for k in keys if context.get(k) not in (None, {}, [])}


def extract_intent(message: str, context: dict, session: Session | None = None) -> IntentResult:
    """LLM intent/entity extraction, validated. Raises LLMUnavailable if no provider answers with valid JSON."""
    summary = _context_summary(context)
    prompt = (
        "Classify this message from a Dhaka commuter (English or Banglish) for a transit assistant.\n"
        f"Allowed intents: {', '.join(INTENTS)}.\n"
        "Rules: copy hub names exactly as the user wrote them; NEVER output route ids, fares, durations or hub ids; "
        "use null when something is not said; 'Mirpur' and 'Uttara' are valid hub words.\n"
        f"Conversation context: {json.dumps(summary)}\nMessage: {message}")
    data = call_llm_json(prompt, INTENT_SCHEMA, session=session, message=message, context_summary=summary)
    provider = data.pop("_provider", "llm")
    try:
        result = IntentResult.model_validate({**data, "source": f"llm:{provider}"})
    except ValidationError as exc:
        raise LLMUnavailable(f"invalid intent JSON: {exc.errors()[0]['msg']}") from exc
    return result


def rewrite_grounded_reply(facts: dict, language: str, session: Session | None = None) -> str:
    """Ask the LLM to phrase already-verified facts naturally. The caller must still check the result adds no new number."""
    lang = {"en": "English", "banglish": "Banglish (Bangla in Latin letters)", "bn": "Bangla script"}.get(language, "English")
    prompt = (f"Rewrite the following verified facts as one short, friendly chat reply in {lang}. Do not add, change or "
              "round any number, hub name or route; do not add facts.\n" + json.dumps(facts, ensure_ascii=False, default=str))
    data = call_llm_json(prompt, REWRITE_SCHEMA, session=session, message=prompt, context_summary={"task": "rewrite"})
    text = data.get("reply_text")
    if not isinstance(text, str) or not text.strip() or len(text) > 1200:
        raise LLMUnavailable("rewrite is empty or too long")
    return text.strip()
