from sqlalchemy import func, select

from app.collector.factory import MissingPlacesApiKey, get_provider
from app.config import get_settings
from app.database import get_session_factory, init_db, reset_engine
from app.models import City, Job, Listing
from app.services import enqueue_job, get_or_create_city
from app.worker import run_job


def test_provider_refuses_without_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "")
    get_settings.cache_clear()
    try:
        try:
            get_provider()
            raise AssertionError("expected MissingPlacesApiKey")
        except MissingPlacesApiKey as exc:
            assert "GOOGLE_PLACES_API_KEY" in str(exc)
    finally:
        get_settings.cache_clear()


def test_job_fails_without_places_key(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "")
    monkeypatch.setenv("JOB_REQUEST_DELAY_SECONDS", "0")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        city = get_or_create_city(session, "Кемерово")
        job = enqueue_job(
            session,
            city_id=city.id,
            category_id=None,
            source="google_places",
            created_via="test",
            created_by="pytest",
        )
        session.commit()
        job_id = job.id
    finally:
        session.close()

    run_job(job_id)

    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert "GOOGLE_PLACES_API_KEY" in job.message
        assert (session.scalar(select(func.count(Listing.id))) or 0) == 0
        seeded_cities = list(session.scalars(select(City)).all())
        assert all(c.slug not in {"novosibirsk", "tomsk", "berdsk"} for c in seeded_cities)
    finally:
        session.close()
        get_settings.cache_clear()
        reset_engine()


def test_init_db_purges_demo_listings(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        city = get_or_create_city(session, "Бердск")
        session.add(
            Listing(
                city_id=city.id,
                dedupe_key="demo",
                name="Кофейня без карточки",
                source="demo",
                place_id="demo:nsk:none",
                maps_status="no_listing",
            )
        )
        session.commit()
        assert (session.scalar(select(func.count(Listing.id))) or 0) == 1
    finally:
        session.close()

    init_db()
    session = SessionLocal()
    try:
        assert (session.scalar(select(func.count(Listing.id))) or 0) == 0
    finally:
        session.close()
        get_settings.cache_clear()
        reset_engine()
