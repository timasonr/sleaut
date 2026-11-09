from types import SimpleNamespace

from app.collector.google_places import (
    GooglePlacesProvider,
    business_queries,
    is_locality_place,
    radius_from_viewport,
)
from app.config import Settings


def test_all_categories_query_is_not_bare_city():
    queries = business_queries("Москва", None)
    assert queries[0] == "организации в Москва"
    assert "Москва" not in queries
    assert all("Москва" in q for q in queries)


def test_category_query_uses_city():
    cat = SimpleNamespace(query_ru="кафе", name="Кафе")
    assert business_queries("Томск", cat)[0] == "кафе в Томск"


def test_skip_city_locality_row():
    assert is_locality_place(["locality", "political"], "Москва", "Москва") is True
    assert is_locality_place(["cafe", "food"], "Кофе на Тверской", "Москва") is False


def test_search_pages_and_skips_locality(monkeypatch):
    settings = Settings(google_places_api_key="test-key", job_max_pages=3, job_request_delay_seconds=0)
    provider = GooglePlacesProvider(settings)
    city = SimpleNamespace(name="Москва", country="RU", lat=0.0, lng=0.0, radius_m=12000, id=4)
    calls = []

    def fake_post(self, url, headers=None, json=None):
        calls.append(json)
        text_query = (json or {}).get("textQuery", "")
        if ", RU" in text_query or text_query.startswith("Москва, "):
            body = {
                "places": [
                    {
                        "id": "city1",
                        "displayName": {"text": "Москва"},
                        "types": ["locality", "political"],
                        "location": {"latitude": 55.7558, "longitude": 37.6173},
                        "viewport": {
                            "low": {"latitude": 55.5, "longitude": 37.3},
                            "high": {"latitude": 55.95, "longitude": 37.9},
                        },
                    }
                ]
            }
        elif "pageToken" in (json or {}):
            body = {
                "places": [
                    {
                        "id": f"p{i}",
                        "displayName": {"text": f"Место {i}"},
                        "types": ["store"],
                        "formattedAddress": "Москва",
                        "location": {"latitude": 55.75, "longitude": 37.61},
                    }
                    for i in range(11, 21)
                ]
            }
        else:
            body = {
                "places": [
                    {
                        "id": "city1",
                        "displayName": {"text": "Москва"},
                        "types": ["locality", "political"],
                        "formattedAddress": "Москва",
                    }
                ]
                + [
                    {
                        "id": f"p{i}",
                        "displayName": {"text": f"Место {i}"},
                        "types": ["cafe"],
                        "formattedAddress": "Москва",
                        "location": {"latitude": 55.75, "longitude": 37.61},
                    }
                    for i in range(1, 11)
                ],
                "nextPageToken": "tok-2",
            }
        return SimpleNamespace(status_code=200, json=lambda: body, text="")

    monkeypatch.setattr("httpx.Client.post", fake_post)
    monkeypatch.setattr("app.collector.google_places.time.sleep", lambda *_a, **_k: None)

    drafts = list(
        provider.search(
            city=city,
            category=None,
            limit=20,
            delay_seconds=0,
            should_stop=lambda: False,
        )
    )
    assert city.lat == 55.7558
    assert city.lng == 37.6173
    assert city.radius_m >= 8000
    names = [d.name for d in drafts]
    assert "Москва" not in names
    assert len(drafts) == 20
    assert any("pageToken" in (c or {}) for c in calls)
    assert any((c or {}).get("locationBias") for c in calls)
    assert radius_from_viewport(None) == 20000
