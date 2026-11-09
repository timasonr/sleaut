from types import SimpleNamespace

from app.boundaries import PUBLIC_LISTING_FIELDS
from app.completeness import PlaceDraft, classify, heuristic_note
from app.csv_export import listing_row


def test_no_listing_without_place():
    status, signals = classify(PlaceDraft(name="Кафе без профиля", address="Новосибирск"))
    assert status == "no_listing"
    assert signals["maps_profile_exists"] is False


def test_incomplete_missing_several_fields():
    draft = PlaceDraft(
        name="СТО",
        place_id="x",
        maps_url="https://maps.google.com/?cid=x",
        address="ул. Кирова, 1",
        photos_count=0,
        website="",
        public_phone="",
        has_hours=False,
    )
    status, signals = classify(draft)
    assert status == "incomplete"
    assert "photos" in signals["missing_public_fields"]
    assert "website" in signals["missing_public_fields"]


def test_public_listing_reasonably_complete():
    draft = PlaceDraft(
        name="Кофе",
        place_id="y",
        maps_url="https://maps.google.com/?cid=y",
        address="ул. Ленина, 1",
        public_phone="+7 383 000-00-00",
        website="https://example.test",
        photos_count=4,
        has_hours=True,
        rating=4.5,
    )
    status, _ = classify(draft)
    assert status == "public_listing"


def test_heuristic_has_no_sales_language():
    _, signals = classify(
        PlaceDraft(name="Салон", place_id="z", maps_url="https://maps.google.com/?q=z")
    )
    note = heuristic_note("Салон", signals)
    banned = ("напишите нам", "оставьте заявку", "скидк", "продаж", "outreach", "pitch")
    low = note.lower()
    assert all(word not in low for word in banned)
    assert "полнот" in low


def test_csv_allowlist_only():
    listing = SimpleNamespace(
        id=1,
        name="Test",
        category_label="Кафе",
        address="addr",
        public_phone="",
        website="",
        rating=None,
        ratings_count=0,
        photos_count=0,
        maps_url="",
        maps_profile_exists=False,
        maps_status="no_listing",
        has_website=False,
        has_phone=False,
        has_photos=False,
        has_hours=False,
        has_posts=None,
        business_status="",
        source="google_places",
        lat=None,
        lng=None,
        city=SimpleNamespace(name="Новосибирск"),
    )
    row = listing_row(listing)
    assert list(row.keys()) == list(PUBLIC_LISTING_FIELDS)
    assert "owner" not in row
    assert "email" not in row
    assert "pitch" not in row
