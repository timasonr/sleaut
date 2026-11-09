from app.access import (
    get_or_create_category,
    get_or_create_city,
    is_approved,
    is_super_admin,
    set_approved,
    slugify,
    upsert_user,
)
from app.config import DEFAULT_SUPER_ADMIN_ID, get_settings
from app.database import get_session_factory, init_db, reset_engine
from app.limits import parse_listing_limit
from app.services import enqueue_job, get_or_create_city


def test_parse_listing_limit():
    assert parse_listing_limit("12") == 12
    assert parse_listing_limit(" 7 ") == 7
    assert parse_listing_limit("0") is None
    assert parse_listing_limit("201") is None
    assert parse_listing_limit("abc") is None
    assert parse_listing_limit("") is None


def test_super_admin_and_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        assert is_super_admin(DEFAULT_SUPER_ADMIN_ID)
        assert is_approved(session, DEFAULT_SUPER_ADMIN_ID)
        other = upsert_user(session, 111, username="wait", display_name="Wait")
        assert other.approved is False
        assert is_approved(session, 111) is False
        set_approved(session, 111, approved=True, by="test")
        session.commit()
        assert is_approved(session, 111) is True
    finally:
        session.close()
        get_settings.cache_clear()
        reset_engine()


def test_get_or_create_city_reuses_cyrillic_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        assert slugify("Москва") == "москва-2d4e5321"
        first = get_or_create_city(session, "Москва")
        session.commit()
        first_id, first_slug = first.id, first.slug
        again = get_or_create_city(session, "москва")
        spaced = get_or_create_city(session, "  Москва ")
        session.commit()
        assert again.id == first_id
        assert spaced.id == first_id
        assert first_slug == "москва-2d4e5321"
        cafe = get_or_create_category(session, "Кафе")
        session.commit()
        assert get_or_create_category(session, "кафе").id == cafe.id
    finally:
        session.close()
        get_settings.cache_clear()
        reset_engine()


def test_enqueue_stores_typed_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        city = get_or_create_city(session, "Томск")
        job = enqueue_job(
            session,
            city_id=city.id,
            category_id=None,
            source="google_places",
            created_via="test",
            created_by="pytest",
            max_results=9,
        )
        session.commit()
        assert job.progress_total == 9
    finally:
        session.close()
        get_settings.cache_clear()
        reset_engine()
