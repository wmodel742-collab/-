"""Просмотр и управление графиком дежурного (этап 5, UX для админа)."""

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
    confirm_cycle_keyboard,
    confirm_swap_keyboard,
    cycle_first_curator_keyboard,
    cycle_second_curator_keyboard,
    day_manage_keyboard,
    manage_hub_keyboard,
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
    day_pick = State()


def _can_view(user: User) -> bool:
    return user_has_role(user, UserRole.CURATOR, UserRole.ADMIN)


def _can_manage(user: User) -> bool:
    return user_has_role(user, UserRole.ADMIN)


def _parse_ym(raw: str) -> tuple[int, int]:
    year_s, month_s = raw.split("-", 1)
    return int(year_s), int(month_s)


def _pick_mode_from_state(state_name: str | None) -> str | None:
    if not state_name:
        return None
    if state_name.endswith(":swap_second"):
        return "swap"
    if state_name.endswith(":cycle_date"):
        return "cycle"
    if state_name.endswith(":day_pick"):
        return "day"
    return None


async def _calendar_ym(session: Session, state: FSMContext) -> tuple[int, int]:
    data = await state.get_data()
    if data.get("cal_year") and data.get("cal_month"):
        return int(data["cal_year"]), int(data["cal_month"])
    today = schedule_service.school_today(session)
    return today.year, today.month


async def _remember_month(state: FSMContext, year: int, month: int) -> None:
    await state.update_data(cal_year=year, cal_month=month)


SCHEDULE_HELP_RU = (
    "<b>Как настроить график дежурного</b>\n\n"
    "Дежурный (менеджер смены) — куратор, которому сейчас падают заявки.\n\n"
    "1. <b>Назначить дежурного на день</b> — разовая подмена на одну дату "
    "(* в календаре). Общий цикл 2/2 не ломается. "
    "«Убрать подмену» возвращает день к циклу.\n"
    "2. <b>Поменять два дня местами</b> — обмен дежурными между двумя датами "
    "(с подтверждением).\n"
    "3. <b>Задать цикл 2/2</b> — с выбранной даты: кто дежурит первые 2 дня, "
    "кто следующие 2, дальше чередование. Прошлое не переписывается.\n\n"
    "Открывайте <b>календарь</b>, чтобы видеть картину месяца. "
    "Квадратные скобки [ ] — сегодня."
)


async def _show_hub(
    target: Message,
    session: Session,
    *,
    notice: str | None = None,
) -> None:
    text = schedule_service.format_manage_hub(session)
    if notice:
        text = f"{notice}\n\n{text}"
    await target.answer(
        text, parse_mode="HTML", reply_markup=manage_hub_keyboard()
    )


async def _edit_hub(
    callback: CallbackQuery,
    session: Session,
    *,
    notice: str | None = None,
) -> None:
    assert callback.message is not None
    text = schedule_service.format_manage_hub(session)
    if notice:
        text = f"{notice}\n\n{text}"
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=manage_hub_keyboard()
    )


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
        text += "\n\n🔄 Шаг: нажмите день в кнопках ниже."
    elif pick_mode == "cycle":
        text += "\n\n📐 Нажмите день, с которого начнётся новый цикл 2/2."
    elif pick_mode == "day":
        text += "\n\n👤 Нажмите день, на который назначить дежурного."
    if notice:
        text = f"{notice}\n\n{text}"
    today = schedule_service.school_today(session)
    duties = schedule_service.month_duty_map(session, year, month)
    await target.answer(
        text,
        parse_mode="HTML",
        reply_markup=month_days_keyboard(
            year,
            month,
            duties,
            manage=manage,
            pick_mode=pick_mode,
            today=today,
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
        text += "\n\n🔄 Шаг: нажмите день в кнопках ниже."
    elif pick_mode == "cycle":
        text += "\n\n📐 Нажмите день старта нового цикла 2/2."
    elif pick_mode == "day":
        text += "\n\n👤 Нажмите день, на который назначить дежурного."
    if notice:
        text = f"{notice}\n\n{text}"
    today = schedule_service.school_today(session)
    duties = schedule_service.month_duty_map(session, year, month)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=month_days_keyboard(
            year,
            month,
            duties,
            manage=manage,
            pick_mode=pick_mode,
            today=today,
        ),
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


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


@router.message(
    F.text.in_(
        {
            "Управление графиком",
            "График дежурного",
            "Настроить график дежурного",
        }
    )
)
async def open_manage_schedule(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await message.answer(
            "Настройка графика дежурного доступна только администратору."
        )
        return
    await state.clear()
    schedule_service.ensure_default_cycle(session)
    session.commit()
    await _show_hub(message, session)


# ---------------------------------------------------------------------------
# Hub / help / abort
# ---------------------------------------------------------------------------


@router.callback_query(F.data == SchedCB.HUB)
async def schedule_hub(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Только для администратора.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await _edit_hub(callback, session)


@router.callback_query(F.data == SchedCB.ABORT)
async def schedule_abort(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Только для администратора.", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    await _edit_hub(callback, session, notice="❌ Действие отменено.")


@router.callback_query(F.data == SchedCB.HELP)
async def schedule_help(callback: CallbackQuery, db_user: User) -> None:
    if not _can_manage(db_user):
        await callback.answer("Только для администратора.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            SCHEDULE_HELP_RU,
            parse_mode="HTML",
            reply_markup=manage_hub_keyboard(),
        )


@router.callback_query(F.data == SchedCB.CAL)
async def schedule_open_calendar(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Только для администратора.", show_alert=True)
        return
    await state.clear()
    year, month = await _calendar_ym(session, state)
    today = schedule_service.school_today(session)
    year, month = today.year, today.month
    await _remember_month(state, year, month)
    await callback.answer()
    await _edit_month(callback, session, year, month, manage=True)


@router.callback_query(F.data == SchedCB.CANCEL)
async def schedule_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ---------------------------------------------------------------------------
# Month navigation (keeps pick_mode)
# ---------------------------------------------------------------------------


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
    pick_mode = _pick_mode_from_state(await state.get_state())
    if pick_mode is None and manage:
        # в обычном manage не сбрасываем cal_* лишний раз
        pass
    elif pick_mode is None:
        await state.clear()
    today = schedule_service.school_today(session)
    await _remember_month(state, today.year, today.month)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        today.year,
        today.month,
        manage=manage,
        pick_mode=pick_mode if manage else None,
    )


@router.callback_query(F.data.startswith(SchedCB.MONTH))
async def schedule_month_nav(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    raw = (callback.data or "").removeprefix(SchedCB.MONTH)
    ym, flag = raw.rsplit(":", 1)
    manage = flag == "m"
    if manage and not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not manage and not _can_view(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    pick_mode = _pick_mode_from_state(await state.get_state())
    if pick_mode is None and not manage:
        await state.clear()
    year, month = _parse_ym(ym)
    await _remember_month(state, year, month)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        year,
        month,
        manage=manage,
        pick_mode=pick_mode if manage else None,
    )


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
    # Выход из экрана дня — обычный календарь manage
    await state.clear()
    year, month = _parse_ym(ym)
    await _remember_month(state, year, month)
    await callback.answer()
    await _edit_month(callback, session, year, month, manage=manage)


# ---------------------------------------------------------------------------
# Day assign
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{SchedCB.DAY}pick")
async def schedule_day_pick_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    year, month = await _calendar_ym(session, state)
    await state.set_state(ScheduleFSM.day_pick)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        year,
        month,
        manage=True,
        pick_mode="day",
        notice="👤 Шаг 1/2: выберите день в календаре.",
    )


@router.callback_query(F.data.startswith(SchedCB.DAY))
async def schedule_day_click(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    raw = (callback.data or "").removeprefix(SchedCB.DAY)
    if raw == "pick":
        return
    iso, flag = raw.rsplit(":", 1)
    on_date = date.fromisoformat(iso)
    manage = flag == "m"
    info = schedule_service.get_duty_info(session, on_date)
    if not manage:
        if info is None:
            await callback.answer("Дежурный не задан", show_alert=True)
            return
        src = "разовая подмена" if info.from_override else "по циклу 2/2"
        await callback.answer(
            f"{on_date.strftime('%d.%m.%Y')}: {info.curator.name} ({src})",
            show_alert=True,
        )
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
        "Кто будет дежурным (менеджером) в этот день?",
        "",
    ]
    if info:
        src = "разовая подмена" if info.from_override else "по циклу 2/2"
        lines.append(f"Сейчас: <b>{info.curator.name}</b> ({src})")
        if info.cycle_curator and info.from_override:
            lines.append(f"По циклу было бы: {info.cycle_curator.name}")
    else:
        lines.append("Дежурный ещё не задан — сначала задайте цикл 2/2.")

    lines.append("")
    lines.append("Выберите куратора ниже или уберите подмену.")

    await state.clear()
    await _remember_month(state, on_date.year, on_date.month)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=day_manage_keyboard(
                on_date,
                curators,
                current_id=info.curator.id if info else None,
                has_override=bool(info and info.from_override),
            ),
        )


@router.callback_query(F.data.startswith(SchedCB.SET))
async def schedule_set_override(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
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

    info = schedule_service.get_duty_info(session, on_date)
    # Уже этот человек по циклу без подмены — ничего не делаем
    if (
        info is not None
        and not info.from_override
        and info.curator.id == curator.id
    ):
        await callback.answer("Уже дежурит по циклу", show_alert=True)
        await state.clear()
        await _remember_month(state, on_date.year, on_date.month)
        await _edit_month(
            callback,
            session,
            on_date.year,
            on_date.month,
            manage=True,
            notice=(
                f"ℹ️ {on_date.strftime('%d.%m.%Y')}: {curator.name} "
                "уже дежурит по циклу 2/2."
            ),
        )
        return

    try:
        schedule_service.set_day_override(
            session, on_date=on_date, curator=curator, actor=db_user
        )
        session.commit()
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await _remember_month(state, on_date.year, on_date.month)
    await callback.answer("Дежурный обновлён")
    await _edit_month(
        callback,
        session,
        on_date.year,
        on_date.month,
        manage=True,
        notice=(
            f"✅ {on_date.strftime('%d.%m.%Y')}: дежурный "
            f"<b>{curator.name}</b> (разовая подмена)."
        ),
    )


@router.callback_query(F.data.startswith(SchedCB.RST))
async def schedule_reset_day(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
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
        notice = f"↩ {on_date.strftime('%d.%m.%Y')}: подмена убрана"
        if info:
            notice += f", снова по циклу — <b>{info.curator.name}</b>"
    else:
        notice = f"Для {on_date.strftime('%d.%m.%Y')} подмены не было."
    await state.clear()
    await _remember_month(state, on_date.year, on_date.month)
    await callback.answer("Готово")
    await _edit_month(
        callback, session, on_date.year, on_date.month, manage=True, notice=notice
    )


# ---------------------------------------------------------------------------
# Swap with confirmation
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{SchedCB.SWAP}start")
async def schedule_swap_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    year, month = await _calendar_ym(session, state)
    await state.set_state(ScheduleFSM.swap_second)
    await state.update_data(swap_first=None)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        year,
        month,
        manage=True,
        pick_mode="swap",
        notice="🔄 Шаг 1/3: нажмите <b>первый</b> день для обмена.",
    )


@router.callback_query(F.data.startswith(f"{SchedCB.SWAP}ok:"))
async def schedule_swap_confirm(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(f"{SchedCB.SWAP}ok:")
    iso_a, iso_b = raw.split(":", 1)
    day_a = date.fromisoformat(iso_a)
    day_b = date.fromisoformat(iso_b)
    try:
        info_a, info_b = schedule_service.swap_two_days(
            session, day_a=day_a, day_b=day_b, actor=db_user
        )
        session.commit()
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await _remember_month(state, day_a.year, day_a.month)
    await callback.answer("Смены поменяны")
    notice = (
        f"🔄 Обмен выполнен:\n"
        f"{day_a.strftime('%d.%m.%Y')} → <b>{info_a.curator.name}</b>\n"
        f"{day_b.strftime('%d.%m.%Y')} → <b>{info_b.curator.name}</b>"
    )
    await _edit_month(
        callback, session, day_a.year, day_a.month, manage=True, notice=notice
    )


@router.callback_query(F.data.startswith(SchedCB.SWAP))
async def schedule_swap_pick(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(SchedCB.SWAP)
    if raw == "start" or raw.startswith("ok:"):
        return
    picked = date.fromisoformat(raw)
    data = await state.get_data()
    first_iso = data.get("swap_first")
    if not first_iso:
        await state.update_data(swap_first=picked.isoformat())
        await state.set_state(ScheduleFSM.swap_second)
        await _remember_month(state, picked.year, picked.month)
        info = schedule_service.get_duty_info(session, picked)
        who = info.curator.name if info else "—"
        await callback.answer(f"Первый день: {picked.strftime('%d.%m')}")
        await _edit_month(
            callback,
            session,
            picked.year,
            picked.month,
            manage=True,
            pick_mode="swap",
            notice=(
                f"🔄 Шаг 2/3: первый день <b>{picked.strftime('%d.%m.%Y')}</b> "
                f"({who}).\nНажмите <b>второй</b> день."
            ),
        )
        return

    day_a = date.fromisoformat(str(first_iso))
    day_b = picked
    if day_a == day_b:
        await callback.answer("Выберите другой день", show_alert=True)
        return

    info_a = schedule_service.get_duty_info(session, day_a)
    info_b = schedule_service.get_duty_info(session, day_b)
    if info_a is None or info_b is None:
        await callback.answer(
            "На обоих днях должен быть дежурный (задайте цикл).",
            show_alert=True,
        )
        return

    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔄 <b>Подтвердите обмен</b>\n\n"
            f"{day_a.strftime('%d.%m.%Y')}: сейчас <b>{info_a.curator.name}</b> "
            f"→ станет <b>{info_b.curator.name}</b>\n"
            f"{day_b.strftime('%d.%m.%Y')}: сейчас <b>{info_b.curator.name}</b> "
            f"→ станет <b>{info_a.curator.name}</b>",
            parse_mode="HTML",
            reply_markup=confirm_swap_keyboard(day_a, day_b),
        )


# ---------------------------------------------------------------------------
# New cycle with confirmation
# ---------------------------------------------------------------------------


@router.callback_query(F.data == f"{SchedCB.CYC}start")
async def schedule_cycle_start(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    year, month = await _calendar_ym(session, state)
    await state.set_state(ScheduleFSM.cycle_date)
    await callback.answer()
    await _edit_month(
        callback,
        session,
        year,
        month,
        manage=True,
        pick_mode="cycle",
        notice=(
            "📐 Шаг 1/3: нажмите день старта нового цикла 2/2 "
            "(или напишите дату ДД.ММ.ГГГГ)."
        ),
    )


@router.callback_query(F.data.startswith(f"{SchedCB.CYC}ok:"))
async def schedule_cycle_confirm(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _can_manage(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(f"{SchedCB.CYC}ok:")
    iso, first_id_s, second_id_s = raw.split(":", 2)
    on_date = date.fromisoformat(iso)
    first = session.get(User, int(first_id_s))
    second = session.get(User, int(second_id_s))
    if first is None or second is None:
        await callback.answer("Куратор не найден.", show_alert=True)
        return
    try:
        schedule_service.change_cycle_from_date(
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
    await _remember_month(state, on_date.year, on_date.month)
    await callback.answer("Цикл обновлён")
    await _edit_month(
        callback,
        session,
        on_date.year,
        on_date.month,
        manage=True,
        notice=(
            f"📐 Новый цикл с {on_date.strftime('%d.%m.%Y')}: "
            f"<b>{first.name}</b> → <b>{second.name}</b>"
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
    if raw == "start" or raw.startswith("ok:"):
        return

    # Выбор первого куратора → экран второго или подтверждение
    if raw.startswith("u:"):
        _, iso, user_id_s = raw.split(":", 2)
        on_date = date.fromisoformat(iso)
        first = session.get(User, int(user_id_s))
        if first is None:
            await callback.answer("Куратор не найден.", show_alert=True)
            return
        others = [
            c
            for c in schedule_service.list_active_curators(session)
            if c.id != first.id
        ]
        if not others:
            await callback.answer("Нужен второй куратор.", show_alert=True)
            return
        if len(others) == 1:
            second = others[0]
            await callback.answer()
            if callback.message:
                await callback.message.edit_text(
                    "📐 <b>Подтвердите новый цикл 2/2</b>\n\n"
                    f"С даты: <b>{on_date.strftime('%d.%m.%Y')}</b>\n"
                    f"Первые 2 дня: <b>{first.name}</b>\n"
                    f"Следующие 2 дня: <b>{second.name}</b>\n"
                    "Дальше чередование. Дни до этой даты не меняются.",
                    parse_mode="HTML",
                    reply_markup=confirm_cycle_keyboard(
                        on_date, first.id, second.id
                    ),
                )
            return

        await state.update_data(
            cycle_from=on_date.isoformat(), cycle_first_id=first.id
        )
        await state.set_state(ScheduleFSM.cycle_second)
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                f"📐 Шаг 3/3. С {on_date.strftime('%d.%m.%Y')} "
                f"первые 2 дня — <b>{first.name}</b>.\n"
                "Кто дежурит следующие 2 дня?",
                parse_mode="HTML",
                reply_markup=cycle_second_curator_keyboard(
                    on_date, first, others
                ),
            )
        return

    # Второй куратор → подтверждение
    if raw.startswith("u2:"):
        parts = raw.split(":")
        on_date = date.fromisoformat(parts[1])
        first = session.get(User, int(parts[2]))
        second = session.get(User, int(parts[3]))
        if first is None or second is None:
            await callback.answer("Куратор не найден.", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await callback.message.edit_text(
                "📐 <b>Подтвердите новый цикл 2/2</b>\n\n"
                f"С даты: <b>{on_date.strftime('%d.%m.%Y')}</b>\n"
                f"Первые 2 дня: <b>{first.name}</b>\n"
                f"Следующие 2 дня: <b>{second.name}</b>\n"
                "Дальше чередование. Дни до этой даты не меняются.",
                parse_mode="HTML",
                reply_markup=confirm_cycle_keyboard(
                    on_date, first.id, second.id
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
    await _remember_month(state, on_date.year, on_date.month)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"📐 Шаг 2/3. Новый цикл с <b>{on_date.strftime('%d.%m.%Y')}</b>.\n"
            "Кто дежурит <b>первые 2 дня</b>?",
            parse_mode="HTML",
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
    await _remember_month(state, on_date.year, on_date.month)
    await message.answer(
        f"📐 Шаг 2/3. Новый цикл с <b>{on_date.strftime('%d.%m.%Y')}</b>.\n"
        "Кто дежурит <b>первые 2 дня</b>?",
        parse_mode="HTML",
        reply_markup=cycle_first_curator_keyboard(on_date, curators),
    )


@router.message(F.text == "❌ Отмена")
async def schedule_text_cancel(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    current = await state.get_state()
    if current is None or not str(current).startswith("ScheduleFSM"):
        return
    await state.clear()
    if _can_manage(db_user):
        await _show_hub(message, session, notice="❌ Действие отменено.")
        return
    await message.answer(
        "Действие с графиком отменено.",
        reply_markup=main_menu_keyboard(db_user),
    )
