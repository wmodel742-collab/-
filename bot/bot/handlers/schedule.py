"""Просмотр и управление графиком кураторов 2/2 (этап 5)."""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import User, UserRole
from bot.keyboards.menus import main_menu_keyboard
from bot.keyboards.schedule import (
    SchedCB,
    cycle_first_curator_keyboard,
    day_manage_keyboard,
    month_days_keyboard,
)
from bot.middlewares.auth import user_has_role
from bot.services import schedule as schedule_service
from bot.services.tickets import parse_date_ru

router = Router(name="schedule")


class ScheduleFSM(StatesGroup):
    swap_second = State()
    cycle_date = State()
    cycle_second = State()


def _can_view(user: User) -> bool:
    return user_has_role(user, UserRole.CURATOR, UserRole.ADMIN)


def _can_manage(user: User) -> bool:
    return user_has_role(user, UserRole.ADMIN)


def _parse_ym(raw: str) -> tuple[int, int]:
    year_s, month_s = raw.split("-", 1)
    return int(year_s), int(month_s)


async def _show_month(
    target: Message,
    session: Session,
    year: int,
    month: int,
    *,
    manage: bool,
    pick_mode: str | None = None,
    notice: str | None = None,
) -> None:
    text = schedule_service.format_month_calendar(
        session, year, month, manage=manage
    )
    if pick_mode == "swap":
        text += "\n\n🔄 Шаг 1/2: нажмите первый день для обмена."
    elif pick_mode == "cycle":
        text += "\n\n📐 Нажмите день, с которого начнётся новый цикл 2/2."
    if notice:
        text = f"{notice}\n\n{text}"
    duties = schedule_service.month_duty_map(session, year, month)
    await target.answer(
        text,
        reply_markup=month_days_keyboard(
            year, month, duties, manage=manage, pick_mode=pick_mode
        ),
    )


async def _edit_month(
    callback: CallbackQuery,
    session: Session,
    year: int,
    month: int,
    *,
    manage: bool,
    pick_mode: str | None = None,
    notice: str | None = None,
) -> None:
    assert callback.message is not None
    text = schedule_service.format_month_calendar(
        session, year, month, manage=manage
    )
    if pick_mode == "swap":
        text += "\n\n🔄 Нажмите день для обмена сменами."
    elif pick_mode == "cycle":
        text += "\n\n📐 Нажмите день старта нового цикла 2/2."
    if notice:
        text = f"{notice}\n\n{text}"
    duties = schedule_service.month_duty_map(session, year, month)
    await callback.message.edit_text(
        text,
        reply_markup=month_days_keyboard(
            year, month, duties, manage=manage, pick_mode=pick_mode
        ),
    )


@router.message(F.text == "График кураторов")
async def open_curator_schedule(
    message: Message, db_user: User, session: Session
) -> None:
    if not _can_view(db_user):
        await message.answer("График доступен кураторам и администратору.")
        return
    schedule_service.ensure_default_cycle(session)
    session.commit()
    today = schedule_service.school_today(session)
    await _show_month(
        message,
        session,
        today.year,
        today.month,
        manage=False,
    )


SCHEDULE_HELP_RU = (
    "<b>Как настроить график</b>\n"
    "1. Нажмите день в календаре — назначьте дежурного или верните «как по графику».\n"
    "2. «Поменять смены местами» — выберите два дня, кураторы поменяются.\n"
    "3. «Новый цикл 2/2» — выберите дату старта и кто дежурит первые два дня.\n"
    "Звёздочка (*) у дня — разовая подмена, общий цикл не ломается."
)


@router.message(F.text == "Управление графиком")
async def open_manage_schedule(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await message.answer("Управление графиком доступно только администратору.")
        return
    await state.clear()
    schedule_service.ensure_default_cycle(session)
    session.commit()
    today = schedule_service.school_today(session)
    await message.answer(SCHEDULE_HELP_RU, parse_mode="HTML")
    await _show_month(
        message,
        session,
        today.year,
        today.month,
        manage=True,
    )


@router.callback_query(F.data == SchedCB.HELP)
async def schedule_help(callback: CallbackQuery, db_user: User) -> None:
    if not _can_manage(db_user):
        await callback.answer("Только для администратора.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(SCHEDULE_HELP_RU, parse_mode="HTML")


@router.callback_query(F.data == SchedCB.CANCEL)
async def schedule_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith(SchedCB.TODAY))
async def schedule_today(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    flag = (callback.data or "").removeprefix(SchedCB.TODAY)
    manage = flag == "m"
    if manage and not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not manage and not _can_view(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.clear()
    today = schedule_service.school_today(session)
    await callback.answer()
    await _edit_month(
        callback, session, today.year, today.month, manage=manage
    )


@router.callback_query(F.data.startswith(SchedCB.MONTH))
async def schedule_month_nav(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    raw = (callback.data or "").removeprefix(SchedCB.MONTH)
    # YYYY-MM:v|m
    ym, flag = raw.rsplit(":", 1)
    manage = flag == "m"
    if manage and not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not manage and not _can_view(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.clear()
    year, month = _parse_ym(ym)
    await callback.answer()
    await _edit_month(callback, session, year, month, manage=manage)


@router.callback_query(F.data.startswith(SchedCB.BACK))
async def schedule_back(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    raw = (callback.data or "").removeprefix(SchedCB.BACK)
    ym, flag = raw.rsplit(":", 1)
    manage = flag == "m"
    if manage and not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.clear()
    year, month = _parse_ym(ym)
    await callback.answer()
    await _edit_month(callback, session, year, month, manage=manage)


@router.callback_query(F.data.startswith(SchedCB.DAY))
async def schedule_day_click(
    callback: CallbackQuery, db_user: User, session: Session
) -> None:
    raw = (callback.data or "").removeprefix(SchedCB.DAY)
    iso, flag = raw.rsplit(":", 1)
    on_date = date.fromisoformat(iso)
    manage = flag == "m"
    info = schedule_service.get_duty_info(session, on_date)
    if not manage:
        if info is None:
            await callback.answer("Дежурный не задан", show_alert=True)
            return
        src = "разовая подмена" if info.from_override else "по циклу 2/2"
        await callback.answer(f"{on_date.strftime('%d.%m.%Y')}: {info.curator.name} ({src})", show_alert=True)
        return

    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    curators = schedule_service.list_active_curators(session)
    if not curators:
        await callback.answer("Нет активных кураторов.", show_alert=True)
        return

    lines = [
        f"<b>День {on_date.strftime('%d.%m.%Y')}</b>",
        "Кто должен дежурить в этот день?",
    ]
    if info:
        src = "разовая подмена" if info.from_override else "по графику 2/2"
        lines.append(f"Сейчас: <b>{info.curator.name}</b> ({src})")
        if info.cycle_curator and info.from_override:
            lines.append(f"По графику было бы: {info.cycle_curator.name}")
    else:
        lines.append("Дежурный ещё не задан — сначала настройте цикл 2/2.")

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=day_manage_keyboard(on_date, curators),
        )


@router.callback_query(F.data.startswith(SchedCB.SET))
async def schedule_set_override(
    callback: CallbackQuery, db_user: User, session: Session
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(SchedCB.SET)
    iso, user_id_s = raw.rsplit(":", 1)
    on_date = date.fromisoformat(iso)
    curator = session.get(User, int(user_id_s))
    if curator is None:
        await callback.answer("Куратор не найден.", show_alert=True)
        return
    try:
        schedule_service.set_day_override(
            session, on_date=on_date, curator=curator, actor=db_user
        )
        session.commit()
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("Дежурный обновлён")
    await _edit_month(
        callback,
        session,
        on_date.year,
        on_date.month,
        manage=True,
        notice=f"✅ {on_date.strftime('%d.%m.%Y')}: {curator.name} (подмена)",
    )


@router.callback_query(F.data.startswith(SchedCB.RST))
async def schedule_reset_day(
    callback: CallbackQuery, db_user: User, session: Session
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    iso = (callback.data or "").removeprefix(SchedCB.RST)
    on_date = date.fromisoformat(iso)
    cleared = schedule_service.clear_day_override(
        session, on_date=on_date, actor=db_user
    )
    session.commit()
    info = schedule_service.get_duty_info(session, on_date)
    if cleared:
        notice = f"↩ {on_date.strftime('%d.%m.%Y')} возвращён к циклу"
        if info:
            notice += f": {info.curator.name}"
    else:
        notice = f"Для {on_date.strftime('%d.%m.%Y')} подмены не было"
    await callback.answer("Готово")
    await _edit_month(
        callback, session, on_date.year, on_date.month, manage=True, notice=notice
    )


@router.callback_query(F.data == f"{SchedCB.SWAP}start")
async def schedule_swap_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    today = schedule_service.school_today(session)
    await state.set_state(ScheduleFSM.swap_second)
    await state.update_data(swap_first=None)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        today.year,
        today.month,
        manage=True,
        pick_mode="swap",
    )


@router.callback_query(F.data.startswith(SchedCB.SWAP))
async def schedule_swap_pick(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(SchedCB.SWAP)
    if raw == "start":
        return  # handled above
    picked = date.fromisoformat(raw)
    data = await state.get_data()
    first_iso = data.get("swap_first")
    if not first_iso:
        await state.update_data(swap_first=picked.isoformat())
        await state.set_state(ScheduleFSM.swap_second)
        await callback.answer(f"Первый день: {picked.strftime('%d.%m')}")
        await _edit_month(
            callback,
            session,
            picked.year,
            picked.month,
            manage=True,
            pick_mode="swap",
            notice=(
                f"Шаг 2/2: первый день {picked.strftime('%d.%m.%Y')}. "
                "Нажмите второй день."
            ),
        )
        return

    day_a = date.fromisoformat(str(first_iso))
    day_b = picked
    try:
        info_a, info_b = schedule_service.swap_two_days(
            session, day_a=day_a, day_b=day_b, actor=db_user
        )
        session.commit()
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await callback.answer("Смены поменяны")
    notice = (
        f"🔄 Обмен:\n"
        f"{day_a.strftime('%d.%m.%Y')} → {info_a.curator.name}\n"
        f"{day_b.strftime('%d.%m.%Y')} → {info_b.curator.name}"
    )
    await _edit_month(
        callback, session, day_a.year, day_a.month, manage=True, notice=notice
    )


@router.callback_query(F.data == f"{SchedCB.CYC}start")
async def schedule_cycle_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    today = schedule_service.school_today(session)
    await state.set_state(ScheduleFSM.cycle_date)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        today.year,
        today.month,
        manage=True,
        pick_mode="cycle",
        notice=(
            "📐 Новый цикл 2/2: нажмите день старта в календаре "
            "(или напишите дату ДД.ММ.ГГГГ)."
        ),
    )


@router.callback_query(F.data.startswith(SchedCB.CYC))
async def schedule_cycle_callbacks(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(SchedCB.CYC)
    if raw == "start":
        return

    # Выбор первого куратора: u:YYYY-MM-DD:id
    if raw.startswith("u:"):
        _, iso, user_id_s = raw.split(":", 2)
        on_date = date.fromisoformat(iso)
        first = session.get(User, int(user_id_s))
        if first is None:
            await callback.answer("Куратор не найден.", show_alert=True)
            return
        curators = [
            c for c in schedule_service.list_active_curators(session) if c.id != first.id
        ]
        if not curators:
            await callback.answer("Нужен второй куратор.", show_alert=True)
            return
        if len(curators) == 1:
            second = curators[0]
            cycle = schedule_service.change_cycle_from_date(
                session,
                effective_from=on_date,
                first_curator=first,
                second_curator=second,
                actor=db_user,
            )
            session.commit()
            await state.clear()
            await callback.answer("Цикл обновлён")
            await _edit_month(
                callback,
                session,
                on_date.year,
                on_date.month,
                manage=True,
                notice=(
                    f"📐 Новый цикл с {on_date.strftime('%d.%m.%Y')}: "
                    f"{first.name} → {second.name} (id={cycle.id})"
                ),
            )
            return

        await state.update_data(
            cycle_from=on_date.isoformat(), cycle_first_id=first.id
        )
        await state.set_state(ScheduleFSM.cycle_second)
        await callback.answer()
        if callback.message:
            rows = []
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            for curator in curators:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Второй: {curator.name}",
                            callback_data=(
                                f"{SchedCB.CYC}u2:{on_date.isoformat()}:"
                                f"{first.id}:{curator.id}"
                            ),
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=(
                            f"{SchedCB.BACK}{on_date.year:04d}-{on_date.month:02d}:m"
                        ),
                    )
                ]
            )
            await callback.message.edit_text(
                f"С {on_date.strftime('%d.%m.%Y')} первые 2 дня — {first.name}.\n"
                "Кто дежурит следующие 2 дня?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        return

    # Второй куратор при >2: u2:date:firstId:secondId
    if raw.startswith("u2:"):
        parts = raw.split(":")
        # u2, YYYY-MM-DD, firstId, secondId
        on_date = date.fromisoformat(parts[1])
        first = session.get(User, int(parts[2]))
        second = session.get(User, int(parts[3]))
        if first is None or second is None:
            await callback.answer("Куратор не найден.", show_alert=True)
            return
        try:
            cycle = schedule_service.change_cycle_from_date(
                session,
                effective_from=on_date,
                first_curator=first,
                second_curator=second,
                actor=db_user,
            )
            session.commit()
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await state.clear()
        await callback.answer("Цикл обновлён")
        await _edit_month(
            callback,
            session,
            on_date.year,
            on_date.month,
            manage=True,
            notice=(
                f"📐 Новый цикл с {on_date.strftime('%d.%m.%Y')}: "
                f"{first.name} → {second.name} (id={cycle.id})"
            ),
        )
        return

    # Выбор даты в календаре
    on_date = date.fromisoformat(raw)
    curators = schedule_service.list_active_curators(session)
    if len(curators) < 2:
        await callback.answer(
            "Для цикла нужны минимум два активных куратора.", show_alert=True
        )
        return
    await state.update_data(cycle_from=on_date.isoformat())
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"Новый цикл с {on_date.strftime('%d.%m.%Y')}.\n"
            "Кто дежурит первые 2 дня? (потом автоматически чередование 2/2)",
            reply_markup=cycle_first_curator_keyboard(on_date, curators),
        )


@router.message(ScheduleFSM.cycle_date)
async def schedule_cycle_date_text(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await state.clear()
        return
    assert message.text is not None
    try:
        on_date = parse_date_ru(message.text)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    curators = schedule_service.list_active_curators(session)
    if len(curators) < 2:
        await message.answer("Для цикла нужны минимум два активных куратора.")
        await state.clear()
        return
    await state.update_data(cycle_from=on_date.isoformat())
    await message.answer(
        f"Новый цикл с {on_date.strftime('%d.%m.%Y')}.\n"
        "Кто дежурит первые 2 дня? (потом автоматически чередование 2/2)",
        reply_markup=cycle_first_curator_keyboard(on_date, curators),
    )


@router.message(F.text == "❌ Отмена")
async def schedule_text_cancel(
    message: Message, db_user: User, state: FSMContext
) -> None:
    current = await state.get_state()
    if current is None or not str(current).startswith("ScheduleFSM"):
        return
    await state.clear()
    await message.answer(
        "Действие с графиком отменено.",
        reply_markup=main_menu_keyboard(db_user),
    )
