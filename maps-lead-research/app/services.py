from __future__ import annotations

from datetime import datetime
import json

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.access import get_or_create_category, get_or_create_city
from app.completeness import PlaceDraft, classify, dedupe_key, signals_dumps
from app.models import Category, City, Job, JobLog, Listing

__all__ = [
    "get_cities",
    "get_categories",
    "get_or_create_city",
    "get_or_create_category",
    "enqueue_job",
    "request_stop",
    "listings_query",
    "upsert_listing",
    "job_to_dict",
]


def get_cities(session: Session) -> list[City]:
    return list(session.scalars(select(City).order_by(City.name)).all())


def get_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.name)).all())


def enqueue_job(
    session: Session,
    *,
    city_id: int,
    category_id: int | None,
    source: str,
    created_via: str,
    created_by: str,
    max_results: int = 0,
) -> Job:
    running = session.scalar(
        select(func.count(Job.id)).where(Job.status.in_(("pending", "running")))
    )
    job = Job(
        city_id=city_id,
        category_id=category_id,
        status="pending",
        source=source,
        created_via=created_via,
        created_by=created_by,
        message="В очереди" if not running else "В очереди (есть другая задача)",
        progress_total=max(0, int(max_results)),
        progress_done=0,
    )
    session.add(job)
    session.flush()
    session.add(JobLog(job_id=job.id, message="Задача создана"))
    return job


def request_stop(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    if job.status in {"pending"}:
        job.status = "stopped"
        job.finished_at = datetime.utcnow()
        job.message = "Остановлена до запуска"
        session.add(JobLog(job_id=job.id, message=job.message))
    elif job.status == "running":
        job.status = "stopping"
        job.message = "Остановка…"
        session.add(JobLog(job_id=job.id, message="Запрошена остановка"))
    return job


def listings_query(
    session: Session,
    *,
    city_id: int | None = None,
    category: str = "",
    maps_group: str = "",
    q: str = "",
    job_id: int | None = None,
) -> Select[tuple[Listing]]:
    stmt = select(Listing)
    if city_id:
        stmt = stmt.where(Listing.city_id == city_id)
    if category:
        stmt = stmt.where(Listing.category_label == category)
    if maps_group == "has_listing":
        stmt = stmt.where(Listing.maps_status == "public_listing")
    elif maps_group == "no_or_incomplete":
        stmt = stmt.where(Listing.maps_status.in_(("incomplete", "no_listing")))
    if job_id:
        stmt = stmt.where(Listing.job_id == job_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Listing.name.ilike(like),
                Listing.address.ilike(like),
                Listing.category_label.ilike(like),
                Listing.public_phone.ilike(like),
                Listing.website.ilike(like),
            )
        )
    return stmt


def upsert_listing(
    session: Session,
    *,
    job: Job,
    draft: PlaceDraft,
) -> Listing:
    status, signals = classify(draft)
    key = dedupe_key(draft.place_id, draft.name, draft.address)
    existing = session.scalar(
        select(Listing).where(Listing.city_id == job.city_id, Listing.dedupe_key == key)
    )
    now = datetime.utcnow()
    payload = dict(
        job_id=job.id,
        city_id=job.city_id,
        category_id=job.category_id,
        dedupe_key=key,
        place_id=draft.place_id,
        name=draft.name,
        category_label=draft.category_label,
        address=draft.address,
        public_phone=draft.public_phone,
        website=draft.website,
        rating=draft.rating,
        ratings_count=draft.ratings_count,
        photos_count=draft.photos_count,
        lat=draft.lat,
        lng=draft.lng,
        maps_url=draft.maps_url,
        maps_profile_exists=draft.maps_profile_exists,
        maps_status=status,
        has_website=bool(draft.website),
        has_phone=bool(draft.public_phone),
        has_photos=draft.photos_count > 0,
        has_hours=draft.has_hours,
        has_posts=draft.has_posts,
        business_status=draft.business_status,
        types_json=json.dumps(draft.types, ensure_ascii=False),
        signals_json=signals_dumps(signals),
        source=draft.source,
        updated_at=now,
    )
    if existing is None:
        listing = Listing(**payload, created_at=now)
        session.add(listing)
        session.flush()
        return listing
    for field, value in payload.items():
        setattr(existing, field, value)
    session.flush()
    return existing


def job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "message": job.message,
        "source": job.source,
        "city": job.city.name if job.city else "",
        "category": job.category.name if job.category else "все",
        "created_via": job.created_via,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "started_at": job.started_at.isoformat() if job.started_at else "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else "",
    }
