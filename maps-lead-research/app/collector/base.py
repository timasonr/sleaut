from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol

from app.completeness import PlaceDraft
from app.models import Category, City

StopFn = Callable[[], bool]


class PlacesProvider(Protocol):
    source: str
    label: str

    def search(
        self,
        city: City,
        category: Category | None,
        limit: int,
        delay_seconds: float,
        should_stop: StopFn,
        on_progress: Callable[[str], None] | None = None,
    ) -> Iterator[PlaceDraft]:
        ...
