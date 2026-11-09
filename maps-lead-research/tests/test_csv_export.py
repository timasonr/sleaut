from types import SimpleNamespace

from app.boundaries import PUBLIC_LISTING_FIELDS
from app.csv_export import is_real_collected, listing_row, listings_csv_bytes
from app.database import get_session_factory, init_db, reset_engine
from app.models import City, Listing


def _listing(**kwargs):
    city = kwargs.pop("city", SimpleNamespace(name="Новосибирск"))
    defaults = dict(
        id=1,
        name="Кофейня на Ленина",
        category_label="Кафе",
        address="ул. Ленина, 1",
        public_phone="+7 383 000-00-00",
        website="https://example.test",
        rating=4.5,
        ratings_count=10,
        photos_count=3,
        maps_url="https://maps.google.com/?cid=123456",
        maps_profile_exists=True,
        maps_status="public_listing",
        has_website=True,
        has_phone=True,
        has_photos=True,
        has_hours=True,
        has_posts=None,
        business_status="OPERATIONAL",
        source="google_places",
        place_id="places/REAL123",
        lat=55.03,
        lng=82.92,
        city=city,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_csv_allowlist_and_utf8_bom():
    real = _listing()
    demo = _listing(
        id=2,
        name="Кофейня без карточки",
        source="demo",
        place_id="demo:nsk:coffee",
        maps_url="https://maps.google.com/?cid=demo-nsk-coffee-lenina",
        maps_status="no_listing",
        maps_profile_exists=False,
    )
    assert is_real_collected(real) is True
    assert is_real_collected(demo) is False
    body, count = listings_csv_bytes([demo, real])
    assert count == 1
    assert body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.split(",") == list(PUBLIC_LISTING_FIELDS)
    assert "Новосибирск" in text
    assert "Кофейня на Ленина" in text
    assert "cid-demo" not in text
    assert "demo:nsk" not in text
    assert "owner" not in header
    assert "email" not in header
    row = listing_row(real)
    assert list(row.keys()) == list(PUBLIC_LISTING_FIELDS)


def test_csv_skips_demo_rows_in_db(tmp_path, monkeypatch):
    from app.bot_app import _csv_bytes

    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "")
    get_settings = __import__("app.config", fromlist=["get_settings"]).get_settings
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        city = City(slug="manual-city", name="Томск", country="RU", lat=0, lng=0)
        session.add(city)
        session.flush()
        session.add(
            Listing(
                city_id=city.id,
                dedupe_key="demo-row",
                name="Демо кафе",
                source="demo",
                place_id="demo:nsk:x",
                maps_url="https://maps.google.com/?cid=demo-nsk-x",
                maps_status="no_listing",
            )
        )
        session.add(
            Listing(
                city_id=city.id,
                dedupe_key="real-row",
                name="Настоящее кафе",
                source="google_places",
                place_id="places/ABC",
                maps_url="https://maps.google.com/?cid=999",
                maps_status="public_listing",
                maps_profile_exists=True,
                address="пр. Ленина, 1, Томск",
            )
        )
        session.commit()
    finally:
        session.close()

    body, count = _csv_bytes()
    try:
        assert count == 1
        assert body is not None
        assert body.startswith(b"\xef\xbb\xbf")
        decoded = body.decode("utf-8-sig")
        assert "Настоящее кафе" in decoded
        assert "Демо кафе" not in decoded
        assert "Томск" in decoded
    finally:
        get_settings.cache_clear()
        reset_engine()
