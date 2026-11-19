"""Middleware: сессия БД и проверка роли сотрудника."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.orm import Session, sessionmaker

from bot.db.models import User, UserRole
from bot.services.users import get_active_user_by_telegram_id, write_audit

ACCESS_DENIED_TEXT = (
    "⛔ Доступ запрещён.\n\n"
    "Бот АвоПеренос доступен только сотрудникам школы.\n"
    "Если вы сотрудник — попросите администратора добавить ваш Telegram ID."
)


class DatabaseMiddleware(BaseMiddleware):
    """Кладёт SQLAlchemy-сессию в данные хендлера."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = self.session_factory()
        data["session"] = session
        try:
            result = await handler(event, data)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class AuthMiddleware(BaseMiddleware):
    """Пускает только активных сотрудников; /start тоже требует авторизации."""

    PUBLIC_COMMANDS: frozenset[str] = frozenset()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: Session = data["session"]
        telegram_user = data.get("event_from_user")
        if telegram_user is None:
            return await handler(event, data)

        user = get_active_user_by_telegram_id(session, telegram_user.id)
        data["db_user"] = user

        if user is None:
            write_audit(
                session,
                actor_telegram_id=telegram_user.id,
                action="access_denied",
                details="unauthorized_or_inactive",
            )
            await self._deny(event)
            return None

        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject) -> None:
        if isinstance(event, Message):
            await event.answer(ACCESS_DENIED_TEXT)
        elif isinstance(event, CallbackQuery):
            await event.answer("Доступ запрещён", show_alert=True)
            if event.message:
                await event.message.answer(ACCESS_DENIED_TEXT)


def require_roles(*roles: UserRole) -> frozenset[UserRole]:
    return frozenset(roles)


def user_has_role(user: User, *roles: UserRole) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    return user.role in roles
