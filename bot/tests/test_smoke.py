"""Smoke-тесты: импорт приложения и сборка диспетчера."""

from __future__ import annotations

import os

from bot import __version__
from bot.config import load_settings
from bot.handlers import setup_routers
from bot.main import bootstrap, build_dispatcher
from bot.services.bootstrap import init_db


def test_package_version():
    assert __version__


def test_import_and_routers():
    # Не вызываем setup_routers() здесь: роутеры aiogram нельзя
    # повторно прикреплять. Сборка проверяется в test_bootstrap_and_dispatcher.
    from bot.handlers import (
        employees,
        mass,
        menu,
        replacement,
        schedule,
        settings,
        start,
        stats,
        tickets,
    )

    assert start.router is not None
    assert menu.router is not None
    assert employees.router is not None
    assert tickets.router is not None
    assert mass.router is not None
    assert replacement.router is not None
    assert schedule.router is not None
    assert settings.router is not None
    assert stats.router is not None
    assert setup_routers is not None


def test_load_settings_from_env():
    settings = load_settings()
    assert settings.bot_token
    assert settings.timezone == "Europe/Moscow"
    assert settings.admin_telegram_id == 1001


def test_init_db_smoke(tmp_path):
    url = f"sqlite:///{(tmp_path / 'smoke.db').as_posix()}"
    factory = init_db(url)
    assert factory is not None


def test_bootstrap_and_dispatcher(tmp_path):
    db_file = tmp_path / "app.db"
    from bot.config import Settings

    settings = Settings(
        bot_token=os.environ.get("BOT_TOKEN", "000000000:TEST_TOKEN_FOR_PYTEST_ONLY"),
        admin_telegram_id=1001,
        database_url=f"sqlite:///{db_file.as_posix()}",
        timezone="Europe/Moscow",
        telegram_proxy=None,
    )
    session_factory = bootstrap(settings)
    dp = build_dispatcher(session_factory)
    assert dp is not None
    assert db_file.exists()
