from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(64), default="RU")
    lat: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lng: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    radius_m: Mapped[int] = mapped_column(Integer, default=12000)

    listings: Mapped[list["Listing"]] = relationship(back_populates="city")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    query_ru: Mapped[str] = mapped_column(String(128), nullable=False)
    google_type: Mapped[str] = mapped_column(String(64), default="")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="google_places")
    created_via: Mapped[str] = mapped_column(String(32), default="telegram")
    created_by: Mapped[str] = mapped_column(String(128), default="")
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    city: Mapped[City] = relationship()
    category: Mapped[Category | None] = relationship()
    logs: Mapped[list["JobLog"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    listings: Mapped[list["Listing"]] = relationship(back_populates="job")


class JobLog(Base):
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    job: Mapped[Job] = relationship(back_populates="logs")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("city_id", "dedupe_key", name="uq_listing_city_dedupe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False)
    place_id: Mapped[str] = mapped_column(String(256), default="")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category_label: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(String(512), default="")
    public_phone: Mapped[str] = mapped_column(String(64), default="")
    website: Mapped[str] = mapped_column(String(512), default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0)
    photos_count: Mapped[int] = mapped_column(Integer, default=0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    maps_url: Mapped[str] = mapped_column(String(512), default="")
    maps_profile_exists: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    maps_status: Mapped[str] = mapped_column(String(32), default="incomplete", index=True)
    has_website: Mapped[bool] = mapped_column(Boolean, default=False)
    has_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    has_photos: Mapped[bool] = mapped_column(Boolean, default=False)
    has_hours: Mapped[bool] = mapped_column(Boolean, default=False)
    has_posts: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    business_status: Mapped[str] = mapped_column(String(64), default="")
    types_json: Mapped[str] = mapped_column(Text, default="[]")
    signals_json: Mapped[str] = mapped_column(Text, default="{}")
    ai_note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="google_places")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    city: Mapped[City] = relationship(back_populates="listings")
    category: Mapped[Category | None] = relationship()
    job: Mapped[Job | None] = relationship(back_populates="listings")


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    display_name: Mapped[str] = mapped_column(String(256), default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_super: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
