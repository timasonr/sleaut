from app.notify import format_job_finish_text, notify_job_owner
from app.config import get_settings
from app.database import get_session_factory, init_db, reset_engine
from app.services import enqueue_job, get_or_create_city
from app.worker import run_job


def test_finish_text_done_and_failed():
    assert (
        format_job_finish_text(status="completed", city="Москва", count=12)
        == "Готово. Город: Москва. Карточек: 12."
    )
    assert (
        format_job_finish_text(status="failed", city="Томск", count=0)
        == "Ошибка. Город: Томск. Карточек: 0."
    )


def test_notify_skips_non_telegram_owner(tmp_path, monkeypatch):
    calls = []

    def fake_post(self, url, **kwargs):
        calls.append(url)
        raise AssertionError("must not call Telegram")

    monkeypatch.setattr("httpx.Client.post", fake_post)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        city = get_or_create_city(session, "Москва")
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
    notify_job_owner(job_id)
    assert calls == []
    get_settings.cache_clear()
    reset_engine()
