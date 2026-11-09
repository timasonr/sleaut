from __future__ import annotations

import json
import logging

import httpx

from app.completeness import heuristic_note
from app.config import Settings, get_settings
from app.models import Listing

log = logging.getLogger(__name__)

SYSTEM = (
    "You analyze the completeness of a public Google Maps / Places listing. "
    "Use only the provided public listing fields. "
    "Write in Russian, 4-8 short sentences. "
    "Describe what is filled vs missing on the public profile "
    "(name, address, phone, website, photos, hours, posts if known). "
    "Do not write sales language, pitches, CTAs, or advice on contacting the business. "
    "Do not invent owners, emails, or private data. "
    "End by stating this is a public-profile completeness note, not an outreach message."
)


def build_note(listing: Listing, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    try:
        signals = json.loads(listing.signals_json or "{}")
    except json.JSONDecodeError:
        signals = {}
    if not settings.openai_api_key.strip():
        return heuristic_note(listing.name, signals)
    try:
        return _openai_note(listing, signals, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenAI note failed, falling back to heuristic: %s", exc)
        base = heuristic_note(listing.name, signals)
        return base + "\n(локальный анализ: языковая модель недоступна)"


def _openai_note(listing: Listing, signals: dict, settings: Settings) -> str:
    payload = {
        "name": listing.name,
        "category": listing.category_label,
        "address": listing.address,
        "public_phone_present": bool(listing.public_phone),
        "website_present": bool(listing.website),
        "rating": listing.rating,
        "ratings_count": listing.ratings_count,
        "photos_count": listing.photos_count,
        "maps_profile_exists": listing.maps_profile_exists,
        "maps_status": listing.maps_status,
        "has_hours": listing.has_hours,
        "has_posts": listing.has_posts,
        "business_status": listing.business_status,
        "signals": signals,
        "source": listing.source,
    }
    body = {
        "model": settings.openai_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": "Публичные поля карточки:\n" + json.dumps(payload, ensure_ascii=False),
            },
        ],
    }
    url = settings.openai_api_base.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=40.0) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        response.raise_for_status()
        data = response.json()
    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("empty model response")
    return text
