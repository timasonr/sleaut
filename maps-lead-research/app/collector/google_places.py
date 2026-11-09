from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
import math
import time

import httpx

from app.collector.base import StopFn
from app.completeness import PlaceDraft
from app.config import Settings
from app.models import Category, City

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.photos",
        "places.googleMapsUri",
        "places.regularOpeningHours",
        "places.currentOpeningHours",
        "places.types",
        "places.businessStatus",
        "places.location",
        "places.viewport",
        "nextPageToken",
    ]
)
GEOCODE_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.types",
        "places.location",
        "places.viewport",
    ]
)

LOCALITY_TYPES = {
    "locality",
    "political",
    "administrative_area_level_1",
    "administrative_area_level_2",
    "administrative_area_level_3",
    "country",
    "colloquial_area",
    "plus_code",
}
BUSINESS_HINTS = {
    "establishment",
    "point_of_interest",
    "store",
    "food",
    "restaurant",
    "cafe",
    "shop",
}

# Used only when the admin chose «все» — not a UI preset list.
ALL_CATEGORY_TERMS = (
    "организации",
    "заведения",
    "магазин",
    "кафе",
    "ресторан",
    "услуги",
    "клиника",
    "аптека",
    "автосервис",
    "салон красоты",
)


def business_queries(city_name: str, category: Category | None) -> list[str]:
    city_name = (city_name or "").strip()
    if category is not None:
        term = (category.query_ru or category.name or "").strip()
        return [f"{term} в {city_name}", f"{term} {city_name}"]
    return [f"{term} в {city_name}" for term in ALL_CATEGORY_TERMS]


def is_locality_place(types: list[str], name: str, city_name: str) -> bool:
    tset = {t for t in types if t}
    hints = tset & BUSINESS_HINTS
    locality = bool(tset & LOCALITY_TYPES) and not hints
    if locality:
        return True
    if name.strip().casefold() == (city_name or "").strip().casefold() and not hints:
        return True
    return False


def radius_from_viewport(viewport: dict | None, default: int = 20000) -> int:
    if not viewport:
        return default
    low = viewport.get("low") or {}
    high = viewport.get("high") or {}
    try:
        dlat = abs(float(high.get("latitude")) - float(low.get("latitude")))
        dlng = abs(float(high.get("longitude")) - float(low.get("longitude")))
    except (TypeError, ValueError):
        return default
    # half-diagonal in meters, clamped to Places circle limits
    meters = 0.5 * math.hypot(dlat * 111_000, dlng * 111_000 * 0.6)
    return int(max(8_000, min(50_000, meters or default)))


class GooglePlacesProvider:
    source = "google_places"
    label = "Google Places API"

    def __init__(self, settings: Settings):
        self.settings = settings

    def search(
        self,
        city: City,
        category: Category | None,
        limit: int,
        delay_seconds: float,
        should_stop: StopFn,
        on_progress: Callable[[str], None] | None = None,
    ) -> Iterator[PlaceDraft]:
        limit = max(1, int(limit))
        seen: set[str] = set()
        yielded = 0
        with httpx.Client(timeout=30.0) as client:
            self.ensure_coordinates(client, city, on_progress)
            queries = business_queries(city.name, category)
            for query in queries:
                if yielded >= limit or should_stop():
                    return
                for draft in self._search_query(
                    client,
                    city=city,
                    category=category,
                    query=query,
                    remaining=limit - yielded,
                    delay_seconds=delay_seconds,
                    should_stop=should_stop,
                    on_progress=on_progress,
                ):
                    key = draft.place_id or f"{draft.name}|{draft.address}"
                    if key in seen:
                        continue
                    if is_locality_place(draft.types, draft.name, city.name):
                        if on_progress:
                            on_progress(f"Пропуск карточки города: {draft.name}")
                        continue
                    seen.add(key)
                    yielded += 1
                    if on_progress:
                        on_progress(f"{yielded}/{limit}: {draft.name}")
                    yield draft
                    if yielded >= limit:
                        return

    def geocode(self, city: City, on_progress: Callable[[str], None] | None = None) -> None:
        with httpx.Client(timeout=30.0) as client:
            self.ensure_coordinates(client, city, on_progress)

    def ensure_coordinates(
        self,
        client: httpx.Client,
        city: City,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        if city.lat and city.lng:
            return
        if on_progress:
            on_progress(f"Геокод города: {city.name}")
        payload = self._post_search(
            client,
            body={
                "textQuery": f"{city.name}, {city.country or 'RU'}",
                "languageCode": "ru",
                "regionCode": city.country or "RU",
                "pageSize": 3,
            },
            field_mask=GEOCODE_MASK,
        )
        pick = None
        for raw in payload.get("places") or []:
            types = raw.get("types") or []
            if is_locality_place(types, (raw.get("displayName") or {}).get("text") or "", city.name) or not pick:
                pick = raw
                if is_locality_place(types, (raw.get("displayName") or {}).get("text") or "", city.name):
                    break
        if pick is None:
            raise RuntimeError(f"Не удалось геокодировать город «{city.name}»")
        loc = pick.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if lat is None or lng is None:
            raise RuntimeError(f"Геокод «{city.name}» без координат")
        city.lat = float(lat)
        city.lng = float(lng)
        city.radius_m = radius_from_viewport(pick.get("viewport"), default=20000)
        if on_progress:
            on_progress(f"Координаты {city.name}: {city.lat:.4f}, {city.lng:.4f}, R={city.radius_m} м")

    def _search_query(
        self,
        client: httpx.Client,
        *,
        city: City,
        category: Category | None,
        query: str,
        remaining: int,
        delay_seconds: float,
        should_stop: StopFn,
        on_progress: Callable[[str], None] | None,
    ) -> Iterator[PlaceDraft]:
        if on_progress:
            on_progress(f"Places API text search: «{query}»")
        yielded = 0
        page_token = ""
        pages = 0
        max_pages = min(10, max(self.settings.job_max_pages, (max(1, remaining) + 19) // 20))
        while yielded < remaining and pages < max_pages:
            if should_stop():
                return
            pages += 1
            body: dict = {
                "textQuery": query,
                "languageCode": "ru",
                "regionCode": city.country or "RU",
                "pageSize": min(20, remaining - yielded),
            }
            if city.lat and city.lng:
                body["locationBias"] = {
                    "circle": {
                        "center": {"latitude": city.lat, "longitude": city.lng},
                        "radius": float(city.radius_m or 20000),
                    }
                }
            if page_token:
                body["pageToken"] = page_token
            payload = self._post_search(client, body=body, field_mask=FIELD_MASK)
            places = payload.get("places") or []
            if on_progress:
                on_progress(f"Страница {pages}: {len(places)} мест")
            if not places:
                break
            for raw in places:
                if should_stop() or yielded >= remaining:
                    return
                time.sleep(max(0.2, delay_seconds))
                draft = self._to_draft(raw, category)
                if is_locality_place(draft.types, draft.name, city.name):
                    if on_progress:
                        on_progress(f"Пропуск карточки города: {draft.name}")
                    continue
                yielded += 1
                yield draft
            page_token = payload.get("nextPageToken") or ""
            if not page_token:
                break
            time.sleep(max(delay_seconds, 1.5))

    def _post_search(self, client: httpx.Client, *, body: dict, field_mask: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.settings.google_places_api_key,
            "X-Goog-FieldMask": field_mask,
        }
        response = client.post(SEARCH_URL, headers=headers, json=body)
        if response.status_code == 429:
            time.sleep(15)
            response = client.post(SEARCH_URL, headers=headers, json=body)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Places API HTTP {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    def _to_draft(self, raw: dict, category: Category | None) -> PlaceDraft:
        display = raw.get("displayName") or {}
        name = display.get("text") or raw.get("id") or "без названия"
        photos = raw.get("photos") or []
        hours = raw.get("regularOpeningHours") or raw.get("currentOpeningHours") or {}
        loc = raw.get("location") or {}
        types = raw.get("types") or []
        place_id = raw.get("id") or ""
        maps_url = raw.get("googleMapsUri") or ""
        if place_id and not maps_url:
            maps_url = f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        label = category.name if category else (self._label_from_types(types) or "")
        return PlaceDraft(
            name=name,
            category_label=label,
            address=raw.get("formattedAddress") or "",
            public_phone=raw.get("nationalPhoneNumber") or "",
            website=raw.get("websiteUri") or "",
            rating=raw.get("rating"),
            ratings_count=int(raw.get("userRatingCount") or 0),
            photos_count=len(photos),
            lat=loc.get("latitude"),
            lng=loc.get("longitude"),
            maps_url=maps_url,
            place_id=place_id,
            has_hours=bool(hours.get("weekdayDescriptions") or hours.get("periods")),
            has_posts=None,
            business_status=raw.get("businessStatus") or "",
            types=list(types),
            source="google_places",
        )

    @staticmethod
    def _label_from_types(types: list[str]) -> str:
        mapping = {
            "cafe": "Кафе",
            "coffee_shop": "Кафе",
            "bakery": "Кафе",
            "restaurant": "Кафе",
            "bar": "Кафе",
            "car_repair": "Автосервис",
            "car_dealer": "Автосервис",
            "doctor": "Клиника",
            "hospital": "Клиника",
            "dentist": "Клиника",
            "beauty_salon": "Салон красоты",
            "hair_care": "Салон красоты",
            "pharmacy": "Аптека",
        }
        for t in types:
            if t in mapping:
                return mapping[t]
        return types[0] if types else ""
