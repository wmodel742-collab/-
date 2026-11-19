"""Обработка пунктов главного меню и заглушек следующих этапов."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.db.models import User, UserRole
from bot.keyboards.menus import STUB_SECTIONS, CallbackData, main_menu_keyboard
from bot.middlewares.auth import user_has_role

router = Router(name="menu")

STUB_TEXT = (
    "🛠 Раздел «{title}» в разработке.\n\n"
    "Он появится в следующих этапах. Сейчас доступны: вход, меню по ролям, "
    "управление сотрудниками (админ), переносы, подбор замены, график, "
    "автоматизация и статистика."
)

# Текст кнопки → ключ заглушки и минимальные роли (admin всегда проходит).
# Этапы 2–7: рабочие разделы — в своих handlers. Этап 8 — финальная проверка.
MENU_STUBS: dict[str, tuple[str, tuple[UserRole, ...]]] = {}


@router.message(F.text.in_(set(MENU_STUBS) or {"__never_match_stub__"}))
async def handle_stub_menu(message: Message, db_user: User) -> None:
    assert message.text is not None
    key, roles = MENU_STUBS[message.text]
    if not user_has_role(db_user, *roles):
        await message.answer("Этот пункт меню недоступен для вашей роли.")
        return
    title = STUB_SECTIONS.get(key, message.text)
    await message.answer(STUB_TEXT.format(title=title))


@router.callback_query(F.data.startswith(CallbackData.STUB))
async def handle_stub_callback(callback: CallbackQuery, db_user: User) -> None:
    _ = db_user
    key = (callback.data or "").removeprefix(CallbackData.STUB)
    title = STUB_SECTIONS.get(key, "Раздел")
    await callback.answer()
    if callback.message:
        await callback.message.answer(STUB_TEXT.format(title=title))


@router.callback_query(F.data == f"{CallbackData.MENU}home")
async def menu_home(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu_keyboard(db_user),
        )
