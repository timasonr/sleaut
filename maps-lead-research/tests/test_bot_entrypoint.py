from app.config import get_settings


def test_bot_module_has_commands_and_no_web_panel():
    import app.bot_app as bot

    names = [cmd.command for cmd in bot.BOT_COMMANDS]
    assert names == [
        "start",
        "help",
        "new",
        "progress",
        "stop",
        "listings",
        "export",
    ]
    assert "panel" not in names
    assert hasattr(bot, "main")
    assert "admin_app" not in bot.__dict__
    try:
        import app.admin_app  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("admin_app should be removed")


def test_idle_without_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "")
    get_settings.cache_clear()
    settings = get_settings()
    try:
        assert settings.telegram_enabled is False
    finally:
        get_settings.cache_clear()
