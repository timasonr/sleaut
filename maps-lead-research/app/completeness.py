from __future__ import annotations

from dataclasses import dataclass, field
import json
import re


def _norm(value: str | None) -> str:
    return (value or "").strip()


def dedupe_key(place_id: str, name: str, address: str) -> str:
    if place_id:
        return f"place:{place_id}"
    blob = f"{_norm(name).lower()}|{_norm(address).lower()}"
    blob = re.sub(r"\s+", " ", blob)
    return f"name:{blob[:220]}"


@dataclass
class PlaceDraft:
    """Public listing fields only. Never store owner identity or outreach copy."""

    name: str
    category_label: str = ""
    address: str = ""
    public_phone: str = ""
    website: str = ""
    rating: float | None = None
    ratings_count: int = 0
    photos_count: int = 0
    lat: float | None = None
    lng: float | None = None
    maps_url: str = ""
    place_id: str = ""
    has_hours: bool = False
    has_posts: bool | None = None
    business_status: str = ""
    types: list[str] = field(default_factory=list)
    source: str = "google_places"

    @property
    def maps_profile_exists(self) -> bool:
        return bool(self.place_id or self.maps_url)


def classify(draft: PlaceDraft) -> tuple[str, dict]:
    """Return (maps_status, signals).

    maps_status:
      - public_listing: Maps profile exists and is reasonably complete
      - incomplete: profile exists but obvious public fields are missing
      - no_listing: no public Maps place/profile
    """
    has_website = bool(_norm(draft.website))
    has_phone = bool(_norm(draft.public_phone))
    has_photos = draft.photos_count > 0
    has_hours = bool(draft.has_hours)
    has_rating = draft.rating is not None
    has_address = bool(_norm(draft.address))
    profile = draft.maps_profile_exists

    missing: list[str] = []
    if not has_photos:
        missing.append("photos")
    if not has_website:
        missing.append("website")
    if not has_phone:
        missing.append("phone")
    if not has_hours:
        missing.append("hours")
    if draft.has_posts is False:
        missing.append("posts")

    if not profile:
        status = "no_listing"
    elif len(missing) >= 2:
        status = "incomplete"
    else:
        status = "public_listing"

    signals = {
        "maps_profile_exists": profile,
        "has_place_id": bool(draft.place_id),
        "has_maps_url": bool(draft.maps_url),
        "has_name": bool(_norm(draft.name)),
        "has_address": has_address,
        "has_website": has_website,
        "has_phone": has_phone,
        "has_photos": has_photos,
        "photos_count": draft.photos_count,
        "has_hours": has_hours,
        "has_rating": has_rating,
        "has_posts": draft.has_posts,
        "missing_public_fields": missing,
        "maps_status": status,
        "posts_source": "unknown_places_api",
    }
    return status, signals


def heuristic_note(name: str, signals: dict) -> str:
    filled: list[str] = []
    missing: list[str] = list(signals.get("missing_public_fields") or [])

    pairs = [
        ("has_name", "название"),
        ("has_address", "адрес"),
        ("has_phone", "телефон на карточке"),
        ("has_website", "сайт"),
        ("has_photos", "фото"),
        ("has_hours", "часы работы"),
        ("has_rating", "рейтинг"),
    ]
    for key, label in pairs:
        if signals.get(key):
            filled.append(label)

    profile = "есть" if signals.get("maps_profile_exists") else "нет"
    posts = signals.get("has_posts")
    if posts is True:
        filled.append("публикации")
    elif posts is False and "posts" not in missing:
        missing.append("posts")

    posts_line = {
        True: "публикации на карточке: есть",
        False: "публикации на карточке: нет",
        None: "публикации (Google posts): неизвестно — Places API это поле не отдаёт",
    }[posts]

    status = signals.get("maps_status")
    status_ru = {
        "public_listing": "публичная карточка Maps выглядит относительно полной",
        "incomplete": "публичная карточка есть, но заметно неполная",
        "no_listing": "публичной карточки Maps не найдено",
    }.get(status, status)

    photos = signals.get("photos_count", 0)
    missing_ru = {
        "photos": "фото",
        "website": "сайт",
        "phone": "телефон",
        "hours": "часы работы",
        "posts": "публикации",
    }
    missing_labels = [missing_ru.get(m, m) for m in missing]

    lines = [
        f"Анализ полноты публичной карточки «{name}».",
        f"Профиль Maps: {profile}. Классификация: {status_ru}.",
        f"Заполнено: {', '.join(filled) if filled else 'почти ничего из проверяемых полей'}.",
        f"Фото на карточке: {photos}.",
        f"Не заполнено или отсутствует: {', '.join(missing_labels) if missing_labels else 'явных пробелов по проверяемым полям нет'}.",
        posts_line + ".",
        "Это описание полноты публичной карточки. Не оценка владельца и не коммерческое предложение.",
    ]
    return "\n".join(lines)


def signals_dumps(signals: dict) -> str:
    return json.dumps(signals, ensure_ascii=False)
