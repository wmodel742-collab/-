"""Админ: настройки автоматизации (интервалы, чат, срочность)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import User, UserRole
from bot.keyboards.menus import main_menu_keyboard
from bot.keyboards.settings import SettingsCB, settings_cancel_keyboard, settings_root_keyboard
from bot.middlewares.auth import user_has_role
from bot.services.automation_settings import (
    EDITABLE_KEYS,
    SETTING_LABELS_RU,
    format_settings_summary,
)
from bot.services.schedule import get_setting, set_setting
from bot.services.users import write_audit

router = Router(name="settings")


class SettingsFSM(StatesGroup):
    editing = State()


def _is_admin(user: User) -> bool:
    return user_has_role(user, UserRole.ADMIN)


async def _show_settings(target: Message, session: Session) -> None:
    await target.answer(
        format_settings_summary(session),
        reply_markup=settings_root_keyboard(),
    )


@router.message(F.text == "Настройки")
async def open_settings(message: Message, db_user: User, session: Session, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await message.answer("Этот пункт меню недоступен для вашей роли.")
        return
    await state.clear()
    await _show_settings(message, session)


@router.callback_query(F.data.startswith(SettingsCB.EDIT))
async def settings_edit_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    key = (callback.data or "").removeprefix(SettingsCB.EDIT)
    if key not in EDITABLE_KEYS:
        await callback.answer("Неизвестный параметр.", show_alert=True)
        return
    current = get_setting(session, key) or ""
    label = SETTING_LABELS_RU.get(key, key)
    await state.set_state(SettingsFSM.editing)
    await state.update_data(setting_key=key)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"✏️ {label}\n\nТекущее значение: `{current}`\n\n"
            "Отправьте новое значение одним сообщением.",
            reply_markup=settings_cancel_keyboard(),
            parse_mode="Markdown",
        )


@router.callback_query(F.data == SettingsCB.BACK)
async def settings_cancel(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        await _show_settings(callback.message, session)


@router.message(SettingsFSM.editing)
async def settings_save_value(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await message.answer("Недостаточно прав.")
        await state.clear()
        return
    data = await state.get_data()
    key = data.get("setting_key")
    if key not in EDITABLE_KEYS:
        await state.clear()
        await message.answer("Сессия редактирования устарела.")
        return
    raw = (message.text or "").strip()
    if not raw and key != "work_chat_id":
        await message.answer("Значение пустое. Отправьте ещё раз или нажмите «Отмена».")
        return

    set_setting(session, key, raw)
    write_audit(
        session,
        actor_telegram_id=db_user.telegram_id,
        action="automation_setting_changed",
        details=f"key={key}; value={raw[:80]}",
    )
    session.commit()
    await state.clear()
    label = SETTING_LABELS_RU.get(key, key)
    await message.answer(
        f"✅ Сохранено: {label} = {raw or '(пусто)'}",
        reply_markup=main_menu_keyboard(db_user),
    )
    await _show_settings(message, session)
