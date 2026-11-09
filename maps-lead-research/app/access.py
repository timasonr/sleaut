from __future__ import annotations

from datetime import datetime
import hashlib
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppUser, Category, City


def slugify(name: str) -> str:
    base = re.sub(r"\s+", "-", name.strip().lower())
    base = re.sub(r"[^\w\-]+", "", base, flags=re.UNICODE)[:40] or "item"
    digest = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def is_super_admin(telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    return int(telegram_id) in get_settings().super_admin_ids


def display_from_user(username: str | None, full_name: str | None) -> tuple[str, str]:
    uname = (username or "").lstrip("@")
    display = (full_name or "").strip() or uname or str("")
    return uname, display


def upsert_user(
    session: Session,
    telegram_id: int,
    *,
    username: str = "",
    display_name: str = "",
) -> AppUser:
    user = session.scalar(select(AppUser).where(AppUser.telegram_id == telegram_id))
    super_flag = is_super_admin(telegram_id)
    if user is None:
        user = AppUser(
            telegram_id=telegram_id,
            username=username,
            display_name=display_name,
            approved=super_flag,
            is_super=super_flag,
            approved_at=datetime.utcnow() if super_flag else None,
            approved_by="bootstrap" if super_flag else "",
        )
        session.add(user)
        session.flush()
        return user
    if username:
        user.username = username
    if display_name:
        user.display_name = display_name
    if super_flag:
        user.approved = True
        user.is_super = True
        if user.approved_at is None:
            user.approved_at = datetime.utcnow()
            user.approved_by = user.approved_by or "bootstrap"
    session.flush()
    return user


def seed_super_admin(session: Session) -> None:
    for tid in get_settings().super_admin_ids:
        upsert_user(session, tid, display_name="super-admin")


def is_approved(session: Session, telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    if is_super_admin(telegram_id):
        return True
    user = session.scalar(select(AppUser).where(AppUser.telegram_id == telegram_id))
    return bool(user and user.approved)


def list_users(session: Session) -> list[AppUser]:
    return list(session.scalars(select(AppUser).order_by(AppUser.id.asc())).all())


def set_approved(
    session: Session,
    telegram_id: int,
    *,
    approved: bool,
    by: str,
) -> AppUser | None:
    user = session.scalar(select(AppUser).where(AppUser.telegram_id == telegram_id))
    if user is None:
        return None
    if is_super_admin(user.telegram_id):
        user.approved = True
        user.is_super = True
        return user
    user.approved = approved
    user.approved_by = by
    user.approved_at = datetime.utcnow() if approved else None
    session.flush()
    return user


def _find_named(session: Session, model, name: str, slug: str):
    existing = session.scalar(select(model).where(model.slug == slug))
    if existing is not None:
        return existing
    existing = session.scalar(select(model).where(model.name == name))
    if existing is not None:
        return existing
    needle = name.casefold()
    for row in session.scalars(select(model)).all():
        if (row.name or "").casefold() == needle:
            return row
    return None


def get_or_create_city(session: Session, name: str) -> City:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Пустое название города")
    slug = slugify(name)
    existing = _find_named(session, City, name, slug)
    if existing is not None:
        return existing
    city = City(
        slug=slug,
        name=name,
        country="RU",
        lat=0.0,
        lng=0.0,
        radius_m=12000,
    )
    try:
        with session.begin_nested():
            session.add(city)
            session.flush()
    except IntegrityError:
        existing = _find_named(session, City, name, slug)
        if existing is not None:
            return existing
        raise
    return city


def get_or_create_category(session: Session, name: str) -> Category:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Пустое название категории")
    slug = slugify(name)
    existing = _find_named(session, Category, name, slug)
    if existing is not None:
        return existing
    category = Category(
        slug=slug,
        name=name,
        query_ru=name,
        google_type="",
    )
    try:
        with session.begin_nested():
            session.add(category)
            session.flush()
    except IntegrityError:
        existing = _find_named(session, Category, name, slug)
        if existing is not None:
            return existing
        raise
    return category
