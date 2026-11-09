from app.collector.google_places import GooglePlacesProvider
from app.config import Settings, get_settings


class MissingPlacesApiKey(RuntimeError):
    MESSAGE = (
        "GOOGLE_PLACES_API_KEY не задан. "
        "Демо-сборщик отключён: задача не будет выполнена на тестовых карточках."
    )

    def __init__(self, message: str | None = None):
        super().__init__(message or self.MESSAGE)


def get_provider(settings: Settings | None = None):
    settings = settings or get_settings()
    if not settings.places_configured:
        raise MissingPlacesApiKey()
    return GooglePlacesProvider(settings)
