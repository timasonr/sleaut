from __future__ import annotations

from datetime import datetime
import logging
import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.collector.factory import MissingPlacesApiKey, get_provider
from app.config import get_settings
from app.database import db_lock, get_session_factory, global_lock, init_db
from app.limits import LISTING_LIMIT_MAX, LISTING_LIMIT_MIN
from app.models import City, Job, JobLog
from app.notify import notify_job_owner
from app.services import upsert_listing

log = logging.getLogger(__name__)
WORKER_ID = f"w-{uuid.uuid4().hex[:8]}"
_stop = threading.Event()


def _log(session, job_id: int, message: str) -> None:
    session.add(JobLog(job_id=job_id, message=message[:1000]))
    job = session.get(Job, job_id)
    if job is not None:
        job.message = message[:500]


def claim_job() -> int | None:
    SessionLocal = get_session_factory()
    with global_lock(), db_lock:
        session = SessionLocal()
        try:
            running = session.scalar(select(Job).where(Job.status == "running"))
            if running is not None:
                return None
            job = session.scalar(
                select(Job).where(Job.status == "pending").order_by(Job.id.asc())
            )
            if job is None:
                return None
            job.status = "running"
            job.worker_id = WORKER_ID
            job.started_at = datetime.utcnow()
            job.message = "Запуск"
            session.add(JobLog(job_id=job.id, message=f"Взята воркером {WORKER_ID}"))
            session.commit()
            return job.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _should_stop(job_id: int) -> bool:
    SessionLocal = get_session_factory()
    with db_lock:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            return job is None or job.status in {"stopping", "stopped"}
        finally:
            session.close()


def _fail_job(job_id: int, message: str) -> None:
    SessionLocal = get_session_factory()
    with db_lock:
        session = SessionLocal()
        try:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = "failed"
            job.finished_at = datetime.utcnow()
            job.message = message[:500]
            session.add(JobLog(job_id=job.id, message=job.message))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    notify_job_owner(job_id)


def run_job(job_id: int) -> None:
    settings = get_settings()
    SessionLocal = get_session_factory()
    try:
        provider = get_provider(settings)
    except MissingPlacesApiKey as exc:
        _fail_job(job_id, str(exc))
        return

    with db_lock:
        session = SessionLocal()
        session.expire_on_commit = False
        try:
            job = session.scalar(
                select(Job)
                .options(selectinload(Job.city), selectinload(Job.category))
                .where(Job.id == job_id)
            )
            if job is None:
                return
            job.source = provider.source
            requested = job.progress_total or settings.job_max_results
            limit = max(LISTING_LIMIT_MIN, min(int(requested), LISTING_LIMIT_MAX))
            job.progress_total = limit
            job.progress_done = 0
            _log(
                session,
                job.id,
                f"Источник: {provider.label}. Лимит {limit}. "
                f"Пауза между запросами {settings.job_request_delay_seconds} с.",
            )
            city = job.city
            category = job.category
            session.commit()
            session.expunge_all()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    collected = 0
    try:
        def on_progress(message: str) -> None:
            with db_lock:
                s = SessionLocal()
                try:
                    _log(s, job_id, message)
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
                finally:
                    s.close()

        if hasattr(provider, "geocode"):
            provider.geocode(city, on_progress)
            with db_lock:
                s = SessionLocal()
                try:
                    row = s.get(City, city.id) if getattr(city, "id", None) else None
                    if row is not None:
                        row.lat = float(city.lat or 0)
                        row.lng = float(city.lng or 0)
                        row.radius_m = int(city.radius_m or 20000)
                        s.commit()
                finally:
                    s.close()

        for draft in provider.search(
            city=city,
            category=category,
            limit=limit,
            delay_seconds=settings.job_request_delay_seconds,
            should_stop=lambda: _should_stop(job_id),
            on_progress=on_progress,
        ):
            with db_lock:
                s = SessionLocal()
                try:
                    job = s.get(Job, job_id)
                    if job is None or job.status in {"stopping", "stopped"}:
                        if job is not None:
                            job.status = "stopped"
                            job.finished_at = datetime.utcnow()
                            _log(s, job_id, "Остановлена")
                            s.commit()
                        notify_job_owner(job_id)
                        return
                    upsert_listing(s, job=job, draft=draft)
                    collected += 1
                    job.progress_done = collected
                    job.progress_total = max(job.progress_total, limit)
                    job.message = f"Собрано {collected}/{limit}: {draft.name}"
                    s.commit()
                except Exception:
                    s.rollback()
                    raise
                finally:
                    s.close()

        with db_lock:
            s = SessionLocal()
            try:
                job = s.get(Job, job_id)
                if job is None:
                    return
                if job.status == "stopping":
                    job.status = "stopped"
                    job.message = f"Остановлена, карточек: {collected}"
                else:
                    job.status = "completed"
                    job.progress_done = collected
                    job.progress_total = max(job.progress_total, limit)
                    job.message = f"Готово, карточек: {collected}"
                job.finished_at = datetime.utcnow()
                s.add(JobLog(job_id=job.id, message=job.message))
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()
        notify_job_owner(job_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("Job %s failed", job_id)
        with db_lock:
            s = SessionLocal()
            try:
                job = s.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.finished_at = datetime.utcnow()
                    job.message = f"Ошибка: {exc}"[:500]
                    s.add(JobLog(job_id=job.id, message=job.message))
                    s.commit()
            finally:
                s.close()
        notify_job_owner(job_id)


def worker_loop() -> None:
    log.info("Worker %s started", WORKER_ID)
    while not _stop.is_set():
        try:
            job_id = claim_job()
            if job_id:
                run_job(job_id)
            else:
                time.sleep(1.0)
        except Exception:
            log.exception("Worker loop error")
            time.sleep(2.0)


def start_background_worker() -> threading.Thread:
    init_db()
    thread = threading.Thread(target=worker_loop, name="job-worker", daemon=True)
    thread.start()
    return thread


def stop_worker() -> None:
    _stop.set()
