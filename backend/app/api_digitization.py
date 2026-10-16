"""Signboard / notice photo digitization endpoints.

The endpoint deliberately returns a reviewable draft instead of silently adding
unverified community signage to the transit network.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, File, UploadFile

from app.config import settings
from app.responses import api_error, success

router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:30]
    return []


def _normalise_draft(data: dict[str, Any], source: str) -> dict[str, Any]:
    fares = data.get("fare_rows") if isinstance(data.get("fare_rows"), list) else []
    cleaned_fares = []
    for row in fares[:15]:
        if isinstance(row, dict):
            cleaned_fares.append({"from": str(row.get("from", "")).strip(), "to": str(row.get("to", "")).strip(),
                                  "fare_bdt": row.get("fare_bdt")})
    return {
        "source": source,
        "document_type": str(data.get("document_type") or "route_notice").strip(),
        "route_name": str(data.get("route_name") or "").strip(),
        "operator": str(data.get("operator") or "").strip(),
        "mode": str(data.get("mode") or "bus").strip().lower(),
        "origin": str(data.get("origin") or "").strip(),
        "destination": str(data.get("destination") or "").strip(),
        "stops": _as_list(data.get("stops")),
        "fare_rows": cleaned_fares,
        "raw_text": str(data.get("raw_text") or "").strip()[:4000],
        "confidence": max(0, min(100, int(data.get("confidence") or 0))),
        "review_note": "Check names and fares before submitting; signboard information is community-sourced.",
    }


def _extract_with_openrouter(image: bytes, media_type: str) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise RuntimeError("VISION_NOT_CONFIGURED")
    prompt = """Read this Bangladesh public-transit signboard, fare chart, or handwritten route note. Return ONLY JSON with:
document_type, route_name, operator, mode, origin, destination, stops (array), fare_rows (array of {from,to,fare_bdt}), raw_text, confidence (0-100).
Do not invent unreadable values: use empty strings/arrays and lower confidence. Preserve Bangla text where present."""
    image_data = base64.b64encode(image).decode("ascii")
    body = {
        "model": settings.openrouter_vision_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
        ]}],
    }
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json=body,
        timeout=settings.llm_timeout_s,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    return json.loads(re.sub(r"^```json\s*|\s*```$", "", text.strip(), flags=re.I))


@router.post("/api/digitize/signboard")
async def digitize_signboard(image: UploadFile = File(...)):
    """Extract a structured draft from a signboard image using OpenRouter Vision."""
    if image.content_type not in ALLOWED_TYPES:
        raise api_error("UNSUPPORTED_IMAGE", "Upload a JPG, PNG, or WebP image.", field="image")
    content = await image.read()
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise api_error("INVALID_IMAGE_SIZE", "Image must be between 1 byte and 8 MB.", field="image")
    try:
        draft = _extract_with_openrouter(content, image.content_type)
    except RuntimeError:
        raise api_error("VISION_NOT_CONFIGURED", "Photo extraction needs OPENROUTER_API_KEY on the backend.", field="image")
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise api_error("VISION_EXTRACTION_FAILED", "Could not read the signboard. Try a clearer, well-lit photo.",
                        field="image", details={"reason": type(exc).__name__})
    return success(_normalise_draft(draft, "openrouter_vision"))
