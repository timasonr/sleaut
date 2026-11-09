from __future__ import annotations

import csv
from io import StringIO

from app.boundaries import PUBLIC_LISTING_FIELDS
from app.models import Listing

DEMO_SOURCES = {"demo", "mock", "fixture"}


def is_real_collected(listing: Listing) -> bool:
    """Export only rows that came from a real Places collection, not fixtures."""
    source = (getattr(listing, "source", "") or "").strip().lower()
    if source in DEMO_SOURCES or not source:
        return False
    if source != "google_places":
        return False
    place_id = getattr(listing, "place_id", "") or ""
    maps_url = getattr(listing, "maps_url", "") or ""
    if place_id.startswith("demo:") or "demo:" in place_id:
        return False
    lowered_url = maps_url.lower()
    if "cid-demo" in lowered_url or "cid=demo-" in lowered_url:
        return False
    return True


def listing_row(listing: Listing) -> dict[str, str]:
    values = {
        "id": listing.id,
        "name": listing.name,
        "category": listing.category_label,
        "city": listing.city.name if listing.city else "",
        "address": listing.address,
        "public_phone": listing.public_phone,
        "website": listing.website,
        "rating": "" if listing.rating is None else listing.rating,
        "ratings_count": listing.ratings_count,
        "photos_count": listing.photos_count,
        "maps_url": listing.maps_url,
        "maps_profile_exists": "yes" if listing.maps_profile_exists else "no",
        "maps_status": listing.maps_status,
        "has_website": "yes" if listing.has_website else "no",
        "has_phone": "yes" if listing.has_phone else "no",
        "has_photos": "yes" if listing.has_photos else "no",
        "has_hours": "yes" if listing.has_hours else "no",
        "has_posts": "" if listing.has_posts is None else ("yes" if listing.has_posts else "no"),
        "business_status": listing.business_status,
        "source": listing.source,
        "lat": "" if listing.lat is None else listing.lat,
        "lng": "" if listing.lng is None else listing.lng,
    }
    return {k: "" if values[k] is None else str(values[k]) for k in PUBLIC_LISTING_FIELDS}


def listings_csv(listings: list[Listing]) -> str:
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(PUBLIC_LISTING_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for item in listings:
        if not is_real_collected(item):
            continue
        writer.writerow(listing_row(item))
    return buf.getvalue()


def listings_csv_bytes(listings: list[Listing]) -> tuple[bytes, int]:
    real = [item for item in listings if is_real_collected(item)]
    # utf-8-sig = UTF-8 with BOM so Excel on Windows detects Cyrillic correctly.
    return listings_csv(real).encode("utf-8-sig"), len(real)
