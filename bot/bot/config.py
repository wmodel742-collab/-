"""Настройки приложения из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    """Конфигурация бота."""

    bot_token: str
    admin_telegram_id: int
    database_url: str
    timezone: str
    telegram_proxy: str | None

    @property
    def has_real_token(self) -> bool:
        token = self.bot_token.strip()
        return bool(token) and "REPLACE" not in token.upper() and token != "0"


def _resolve_database_url(raw: str) -> str:
    """Превращает относительный sqlite-путь в абсолютный от корня проекта."""
    if raw.startswith("sqlite:///./"):
        relative = raw.removeprefix("sqlite:///./")
        absolute = (PROJECT_ROOT / relative).resolve()
        return f"sqlite:///{absolute.as_posix()}"
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        path_part = raw.removeprefix("sqlite:///")
        if not path_part.startswith("/"):
            absolute = (PROJECT_ROOT / path_part).resolve()
            return f"sqlite:///{absolute.as_posix()}"
    return raw


def load_settings(env_file: Path | None = None) -> Settings:
    """Загружает настройки из .env и окружения."""
    dotenv_path = env_file or (PROJECT_ROOT / ".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    admin_raw = os.getenv("ADMIN_TELEGRAM_ID", "0").strip()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./avoperenos.db").strip()
    timezone = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    telegram_proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None

    try:
        admin_telegram_id = int(admin_raw)
    except ValueError as exc:
        raise ValueError(
            "ADMIN_TELEGRAM_ID должен быть числом (ваш Telegram ID)."
        ) from exc

    if not bot_token:
        raise ValueError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен."
        )

    return Settings(
        bot_token=bot_token,
        admin_telegram_id=admin_telegram_id,
        database_url=_resolve_database_url(database_url),
        timezone=timezone,
        telegram_proxy=telegram_proxy,
    )
