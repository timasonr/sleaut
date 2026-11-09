from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import db_lock, get_session_factory
from app.models import Job

log = logging.getLogger(__name__)

STATUS_RU = {
    "completed": "Готово",
    "failed": "Ошибка",
    "stopped": "Остановлено",
}

TELEGRAM_SEND = "https://api.telegram.org/bot{token}/sendMessage"


def format_job_finish_text(*, status: str, city: str, count: int) -> str:
    label = STATUS_RU.get(status, status)
    city = city or "—"
    return f"{label}. Город: {city}. Карточек: {count}."


def _owner_chat_id(created_by: str) -> int | None:
    raw = (created_by or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def notify_job_owner(job_id: int) -> None:
    """Tell the Telegram user who started the job that it finished. Not outreach."""
    settings = get_settings()
    if not settings.telegram_enabled:
        return
    SessionLocal = get_session_factory()
    with db_lock:
        session = SessionLocal()
        try:
            job = session.scalar(
                select(Job).options(selectinload(Job.city)).where(Job.id == job_id)
            )
            if job is None:
                return
            chat_id = _owner_chat_id(job.created_by)
            if chat_id is None:
                return
            city = job.city.name if job.city else ""
            count = int(job.progress_done or 0)
            status = job.status
            text = format_job_finish_text(status=status, city=city, count=count)
        finally:
            session.close()

    token = settings.telegram_bot_token.strip()
    if not token:
        return
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                TELEGRAM_SEND.format(token=token),
                json={"chat_id": chat_id, "text": text},
            )
        if response.status_code >= 400:
            log.warning("job finish notify HTTP %s for job %s", response.status_code, job_id)
    except Exception:
        log.warning("job finish notify failed for job %s", job_id)
