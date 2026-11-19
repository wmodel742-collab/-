"""Управление сотрудниками (этап 1, только админ)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import ROLE_LABELS_RU, User, UserRole
from bot.keyboards.menus import (
    CallbackData,
    employee_manage_keyboard,
    employee_roles_keyboard,
)
from bot.services.users import (
    create_employee,
    deactivate_employee,
    format_employee_line,
    list_employees,
    parse_role,
)

router = Router(name="employees")


class AddEmployeeFSM(StatesGroup):
    waiting_telegram_id = State()
    waiting_name = State()
    waiting_role = State()


class DeactivateEmployeeFSM(StatesGroup):
    waiting_telegram_id = State()


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


@router.message(F.text == "Управление сотрудниками")
async def open_employees(message: Message, db_user: User, state: FSMContext) -> None:
    if not _is_admin(db_user):
        await message.answer("Раздел доступен только администратору.")
        return
    await state.clear()
    await message.answer(
        "👥 <b>Управление сотрудниками</b>\n\n"
        "Добавление по Telegram ID, список и деактивация доступа.",
        reply_markup=employee_manage_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == f"{CallbackData.EMP}cancel")
async def cancel_employee_flow(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.answer(
            "Действие отменено.",
            reply_markup=employee_manage_keyboard(),
        )


@router.callback_query(F.data == f"{CallbackData.EMP}list")
async def list_employees_cb(
    callback: CallbackQuery, session: Session, db_user: User
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    employees = list_employees(session, include_inactive=True)
    await callback.answer()
    if not employees:
        text = "Список сотрудников пуст."
    else:
        lines = [format_employee_line(u) for u in employees]
        text = "📋 <b>Сотрудники</b>\n\n" + "\n".join(lines)
    if callback.message:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=employee_manage_keyboard())


@router.callback_query(F.data == f"{CallbackData.EMP}add")
async def start_add_employee(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(AddEmployeeFSM.waiting_telegram_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите <b>Telegram ID</b> сотрудника (только число).\n"
            "Узнать ID можно у бота @userinfobot.",
            parse_mode="HTML",
        )


@router.message(AddEmployeeFSM.waiting_telegram_id)
async def add_employee_id(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой Telegram ID. Попробуйте ещё раз или /start для отмены.")
        return
    await state.update_data(telegram_id=int(raw))
    await state.set_state(AddEmployeeFSM.waiting_name)
    await message.answer("Введите <b>имя</b> сотрудника (как в школе):", parse_mode="HTML")


@router.message(AddEmployeeFSM.waiting_name)
async def add_employee_name(message: Message, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        await state.clear()
        return
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое. Введите ещё раз.")
        return
    await state.update_data(name=name)
    await state.set_state(AddEmployeeFSM.waiting_role)
    await message.answer(
        "Выберите <b>роль</b>:",
        parse_mode="HTML",
        reply_markup=employee_roles_keyboard(),
    )


@router.callback_query(AddEmployeeFSM.waiting_role, F.data.startswith(CallbackData.ROLE))
async def add_employee_role(
    callback: CallbackQuery,
    state: FSMContext,
    session: Session,
    db_user: User,
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        await state.clear()
        return
    role_raw = (callback.data or "").removeprefix(CallbackData.ROLE)
    try:
        role = parse_role(role_raw)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    data = await state.get_data()
    await state.clear()
    try:
        user = create_employee(
            session,
            telegram_id=int(data["telegram_id"]),
            name=str(data["name"]),
            role=role,
            actor_telegram_id=db_user.telegram_id,
        )
    except ValueError as exc:
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                f"⚠️ {exc}",
                reply_markup=employee_manage_keyboard(),
            )
        return

    await callback.answer("Сохранено")
    role_label = ROLE_LABELS_RU[user.role]
    if callback.message:
        await callback.message.answer(
            f"✅ Сотрудник добавлен:\n{user.name} — {role_label}, ID {user.telegram_id}",
            reply_markup=employee_manage_keyboard(),
        )


@router.message(AddEmployeeFSM.waiting_role)
async def add_employee_role_text(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    """Альтернатива: роль текстом, если не нажали кнопку."""
    if not _is_admin(db_user):
        await state.clear()
        return
    try:
        role = parse_role(message.text or "")
    except ValueError as exc:
        await message.answer(f"{exc}\nИли выберите роль кнопкой.")
        return

    data = await state.get_data()
    await state.clear()
    try:
        user = create_employee(
            session,
            telegram_id=int(data["telegram_id"]),
            name=str(data["name"]),
            role=role,
            actor_telegram_id=db_user.telegram_id,
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}", reply_markup=employee_manage_keyboard())
        return

    role_label = ROLE_LABELS_RU[user.role]
    await message.answer(
        f"✅ Сотрудник добавлен:\n{user.name} — {role_label}, ID {user.telegram_id}",
        reply_markup=employee_manage_keyboard(),
    )


@router.callback_query(F.data == f"{CallbackData.EMP}deactivate")
async def start_deactivate(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(DeactivateEmployeeFSM.waiting_telegram_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите <b>Telegram ID</b> сотрудника для деактивации доступа:",
            parse_mode="HTML",
        )


@router.message(DeactivateEmployeeFSM.waiting_telegram_id)
async def deactivate_by_id(
    message: Message, state: FSMContext, session: Session, db_user: User
) -> None:
    if not _is_admin(db_user):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой Telegram ID.")
        return
    telegram_id = int(raw)
    await state.clear()
    try:
        user = deactivate_employee(
            session,
            telegram_id=telegram_id,
            actor_telegram_id=db_user.telegram_id,
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}", reply_markup=employee_manage_keyboard())
        return
    await message.answer(
        f"🚫 Доступ деактивирован: {user.name} (ID {user.telegram_id}).",
        reply_markup=employee_manage_keyboard(),
    )
