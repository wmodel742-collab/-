"""Точка входа Telegram-бота АвоПеренос."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Settings, load_settings
from bot.handlers import setup_routers
from bot.middlewares.auth import AuthMiddleware, DatabaseMiddleware
from bot.services.bootstrap import ensure_admin_from_env, init_db, seed_default_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("avoperenos")


def build_dispatcher(session_factory) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DatabaseMiddleware(session_factory))
    dp.update.middleware(AuthMiddleware())
    dp.include_router(setup_routers())
    return dp


def bootstrap(settings: Settings):
    """Инициализация БД, настроек и первого админа."""
    session_factory = init_db(settings.database_url)
    with session_factory() as session:
        seed_default_settings(session, settings.timezone)
        admin = ensure_admin_from_env(session, settings.admin_telegram_id)
        session.commit()
        if admin is not None:
            logger.info(
                "Админ из .env готов: telegram_id=%s active=%s",
                admin.telegram_id,
                admin.active,
            )
        else:
            logger.warning(
                "ADMIN_TELEGRAM_ID не задан или равен 0 — первого админа не создали."
            )
    return session_factory


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    session_factory = bootstrap(settings)
    if settings.telegram_proxy:
        logger.info("Используется TELEGRAM_PROXY для связи с Telegram API")
        session = AiohttpSession(proxy=settings.telegram_proxy)
        bot = Bot(token=settings.bot_token, session=session)
    else:
        bot = Bot(token=settings.bot_token)
    dp = build_dispatcher(session_factory)

    from bot.services.scheduler import setup_scheduler, start_scheduler, shutdown_scheduler

    scheduler = setup_scheduler(
        database_url=settings.database_url,
        session_factory=session_factory,
        bot=bot,
        timezone=settings.timezone,
    )
    start_scheduler(scheduler)
    logger.info("АвоПеренос запущен. Часовой пояс: %s", settings.timezone)
    try:
        await dp.start_polling(bot)
    finally:
        shutdown_scheduler()
        await bot.session.close()


def main() -> None:
    try:
        settings = load_settings()
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    if not settings.has_real_token:
        logger.error(
            "BOT_TOKEN похож на заглушку. Укажите настоящий токен в файле .env "
            "и перезапустите START_BOT.bat."
        )
        sys.exit(1)

    if settings.admin_telegram_id <= 0:
        logger.error(
            "Укажите ваш числовой ADMIN_TELEGRAM_ID в .env "
            "(узнать можно у @userinfobot)."
        )
        sys.exit(1)

    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
