from __future__ import annotations

LISTING_LIMIT_MIN = 1
LISTING_LIMIT_MAX = 200


def parse_listing_limit(text: str) -> int | None:
    raw = (text or "").strip().replace(" ", "")
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < LISTING_LIMIT_MIN or value > LISTING_LIMIT_MAX:
        return None
    return value
