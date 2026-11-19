"""Команда /start и приветствие."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.orm import Session

from bot.db.models import ROLE_LABELS_RU, User
from bot.keyboards.menus import main_menu_keyboard

router = Router(name="start")


def greeting_text(user: User) -> str:
    role = ROLE_LABELS_RU.get(user.role, user.role.value)
    return (
        f"🥑 Привет, {user.name}!\n\n"
        f"Я бот <b>АвоПеренос</b> (@avoperenos_bot).\n"
        f"Ваша роль: <b>{role}</b>.\n\n"
        "Выберите действие в меню ниже."
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: User, session: Session) -> None:
    # session нужен middleware; db_user уже проверен AuthMiddleware
    _ = session
    await message.answer(
        greeting_text(db_user),
        reply_markup=main_menu_keyboard(db_user),
        parse_mode="HTML",
    )
