# Chat Assistant

Code: `app/services/chat_service.py` (a turn), `nlp_service.py` (rules + `parse_query`), `llm_service.py` (providers),
`app/api_chat.py`. Tests: `test_nlp_service.py`, `test_chat_service.py` (rule parser + mocked LLM; no live key needed).

## One turn (`POST /api/chat {session_id?, message, ui_language?}`)
1. load/create the chat session and its server-side `context_state`;
2. parse intent + entities: LLM JSON (validated with Pydantic) if a key is configured, else/after failure the rule parser;
3. resolve hub names against the database (hub id, name, alias; ambiguous / unknown hubs produce a clarification);
4. call the engines: `routing_service`, `fare_service` (through trip options), `alert_service`, `simulation_service`, `grounding_service`;
5. build `reply_text` from a deterministic template (English / Banglish / Bangla); an LLM may rephrase it, but only a rephrasing that adds no
   new number is kept;
6. ground the final reply (`validate_chat_payload`). If a rephrasing fails, the template is used (`fallback_used=true`). If the engine facts
   themselves fail, the facts are withheld (`valid=false`, `fallback_used=true`);
7. save both messages and the context, publish `chat.message` (session-scoped), return the response.

Response: `session_id, message_id, reply_text, language_detected, intent, entities, actions[{type,payload_key}], structured_payload,
grounding{checked,valid,violations,fallback_used}, context_state, suggested_followups`. The field is `reply_text`, never `reply.text`.
`GET /api/chat/sessions/{id}?limit=` returns recent messages (with payload and grounding) and `context_state`.

## Intents
`find_route`, `request_alternative_route`, `compare_fare`, `submit_disruption_report`, `confirm_report_draft`, `explain_fare_rule`,
`alert_reliability`, `apply_simulation`, `reset_simulation`, `smalltalk`, `unknown`.
- **find_route / alternative:** journey from the message or context; budget, student fare, optimize and avoided hubs persist in
  `context_state` (`last_origin`, `last_destination`, `last_route_id`, `last_budget`, `last_preferences`). The options are also cached so
  `/api/fares/estimate` can price `selected_route_id`. "Alternative" skips routes already shown.
- **compare_fare:** regular vs student breakdown of the context route (or a route restricted to a named mode).
- **submit_disruption_report:** creates a **draft** only (`structured_payload.draft_report`, `confirmation_required`). "confirm" submits it
  as `pending` (needs a verified user; guests can draft but are asked to log in); "cancel" discards it. Nothing becomes verified or affects routing.
- **explain_fare_rule:** a rule card from `fare_rules` / `mode_rates` / `scenario_multipliers`; its numbers are verified against the database.
- **alert_reliability:** `grounding_service.build_reliability_payload` for a report id, the context's last report, or the latest report at a named hub.
- **apply_simulation / reset_simulation:** `simulation_service`; allowed for verified users, or any session id starting with `DEMO`
  (`CHAT_ALLOW_DEMO_SIMULATION`, default on) for the demo script.

## Language
`language_detected` is what the user typed (Bangla script, Banglish markers, else English). Replies: Bangla script if `ui_language="bn"` or the
user typed Bangla; Banglish if the user typed Banglish; otherwise the `ui_language`. Banglish normalization (`theke`, `jete chai/jabo`, `takar`,
`pani joma`, `avoid koro`, `fastest/cheap`, ...) is in `nlp_service.normalize_banglish`. Bangla-script input is detected but not parsed.

## LLM
`LLM_PROVIDER` first, then OpenRouter -> Gemini -> Groq, only providers with a key. Temperature 0, strict JSON, `LLM_TIMEOUT_S` timeout;
timeouts, 429s, HTTP errors and invalid JSON move to the next provider and finally to the rule parser. Responses are cached in `llm_cache`
(per provider and prompt). Models are env-overridable (`OPENROUTER_MODEL`, `GEMINI_MODEL`, `GROQ_MODEL`); the free-tier defaults may need
updating and were not exercised live. The LLM only returns text entities: hub names are resolved by the backend, and route ids, fares and
durations in LLM output are ignored. `CHAT_LLM_REWRITE=false` disables rephrasing.

## Trip search hook
`nlp_service.parse_query(query_text, session_id)` is what `POST /api/trips/search` calls for `query_text` (rules only, no database).
