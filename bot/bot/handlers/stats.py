"""Админ: раздел «Статистика» — преподаватели, ученики, CSV, периоды."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.orm import Session

from bot.db.models import User, UserRole
from bot.keyboards.menus import main_menu_keyboard
from bot.keyboards.stats import (
    StatsCB,
    custom_cancel_keyboard,
    period_keyboard,
    stats_root_keyboard,
    teacher_actions_keyboard,
    teachers_list_keyboard,
    weekly_keyboard,
)
from bot.middlewares.auth import user_has_role
from bot.services.analytics import (
    PeriodKind,
    build_students_csv,
    build_transfers_csv,
    format_students_stats,
    format_teacher_stats,
    format_weekly_summary,
    list_teacher_names,
    parse_date_ru,
    resolve_period,
    rolling_30d_range,
    student_stats_list,
    teacher_stats,
)

router = Router(name="stats")


class StatsFSM(StatesGroup):
    custom_start = State()
    custom_end = State()


def _is_admin(user: User) -> bool:
    return user_has_role(user, UserRole.ADMIN)


async def _show_root(target: Message) -> None:
    await target.answer(
        "📊 <b>Статистика</b>\n\n"
        "По преподавателям и ученикам · 7 / 30 дней, месяц или свой период.\n"
        "Стабильность: скользящие 30 дней по инициативе преподавателя "
        "(отказы, дубли, родитель и школа не считаются).",
        parse_mode="HTML",
        reply_markup=stats_root_keyboard(),
    )


@router.message(F.text == "Статистика")
async def open_stats(
    message: Message, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await message.answer("Этот пункт меню недоступен для вашей роли.")
        return
    await state.clear()
    await _show_root(message)


@router.callback_query(F.data == StatsCB.ROOT)
async def stats_root_cb(
    callback: CallbackQuery, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await _show_root(callback.message)


@router.callback_query(F.data == StatsCB.TEACHERS)
async def stats_teachers(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    names = list_teacher_names(session)
    await state.update_data(teacher_names=names)
    await callback.answer()
    if callback.message:
        if not names:
            await callback.message.answer(
                "Пока нет преподавателей в заявках.",
                reply_markup=stats_root_keyboard(),
            )
            return
        await callback.message.answer(
            "Выберите преподавателя:",
            reply_markup=teachers_list_keyboard(names, page=0),
        )


@router.callback_query(F.data.startswith(StatsCB.TEACHER_PAGE))
async def stats_teachers_page(
    callback: CallbackQuery, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(StatsCB.TEACHER_PAGE)
    try:
        page = int(raw)
    except ValueError:
        await callback.answer("Ошибка страницы", show_alert=True)
        return
    data = await state.get_data()
    names = list(data.get("teacher_names") or [])
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Выберите преподавателя:",
            reply_markup=teachers_list_keyboard(names, page=page),
        )


@router.callback_query(F.data.startswith(StatsCB.TEACHER))
async def stats_teacher_pick(
    callback: CallbackQuery, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if (callback.data or "").startswith(StatsCB.TEACHER_PAGE):
        return
    raw = (callback.data or "").removeprefix(StatsCB.TEACHER)
    try:
        idx = int(raw)
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    await state.update_data(teacher_idx=idx, stats_scope=f"t:{idx}")
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Период для статистики преподавателя:",
            reply_markup=period_keyboard(scope=f"t:{idx}"),
        )


@router.callback_query(F.data == StatsCB.STUDENTS)
async def stats_students_period(
    callback: CallbackQuery, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.update_data(stats_scope="students")
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Период по ученикам:",
            reply_markup=period_keyboard(scope="students"),
        )


@router.callback_query(F.data == StatsCB.WEEKLY)
async def stats_weekly(
    callback: CallbackQuery, db_user: User, session: Session
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    text = format_weekly_summary(session)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            text, parse_mode="HTML", reply_markup=weekly_keyboard()
        )


@router.callback_query(F.data.startswith(StatsCB.PERIOD))
async def stats_period_chosen(
    callback: CallbackQuery,
    db_user: User,
    session: Session,
    state: FSMContext,
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(StatsCB.PERIOD)
    # scope:kind  e.g. students:30d  or t:0:7d
    parts = raw.rsplit(":", 1)
    if len(parts) != 2:
        await callback.answer("Ошибка периода", show_alert=True)
        return
    scope, kind_raw = parts
    try:
        kind = PeriodKind(kind_raw)
    except ValueError:
        await callback.answer("Неизвестный период", show_alert=True)
        return

    if kind == PeriodKind.CUSTOM:
        await state.set_state(StatsFSM.custom_start)
        await state.update_data(stats_scope=scope, period_kind=kind.value)
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Введите <b>дату начала</b> периода (ДД.ММ.ГГГГ):",
                parse_mode="HTML",
                reply_markup=custom_cancel_keyboard(),
            )
        return

    await callback.answer()
    await _render_period_stats(
        callback.message, session, state, scope=scope, kind=kind
    )


async def _render_period_stats(
    message: Message | None,
    session: Session,
    state: FSMContext,
    *,
    scope: str,
    kind: PeriodKind,
    start_date=None,
    end_date=None,
) -> None:
    if message is None:
        return
    date_range = resolve_period(
        session, kind, start_date=start_date, end_date=end_date
    )
    if scope == "students":
        items = student_stats_list(session, date_range=date_range)
        text = format_students_stats(items, date_range=date_range)
        await message.answer(
            text, parse_mode="HTML", reply_markup=stats_root_keyboard()
        )
        return

    if scope.startswith("t:"):
        idx = int(scope.split(":", 1)[1])
        data = await state.get_data()
        names = list(data.get("teacher_names") or list_teacher_names(session))
        if idx < 0 or idx >= len(names):
            await message.answer("Преподаватель не найден.")
            return
        name = names[idx]
        stats = teacher_stats(session, teacher_name=name, date_range=date_range)
        text = format_teacher_stats(stats, date_range=date_range)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=teacher_actions_keyboard(
                teacher_idx=idx, period=kind.value
            ),
        )
        return

    await message.answer("Неизвестный раздел статистики.")


@router.message(StatsFSM.custom_start)
async def custom_start_date(
    message: Message, db_user: User, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await state.clear()
        return
    try:
        start = parse_date_ru(message.text or "")
    except ValueError as exc:
        await message.answer(f"{exc}. Ещё раз или «Отмена».")
        return
    await state.update_data(custom_start=start.isoformat())
    await state.set_state(StatsFSM.custom_end)
    await message.answer(
        "Введите <b>дату конца</b> периода (ДД.ММ.ГГГГ):",
        parse_mode="HTML",
        reply_markup=custom_cancel_keyboard(),
    )


@router.message(StatsFSM.custom_end)
async def custom_end_date(
    message: Message, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await state.clear()
        return
    try:
        end = parse_date_ru(message.text or "")
    except ValueError as exc:
        await message.answer(f"{exc}. Ещё раз или «Отмена».")
        return
    data = await state.get_data()
    from datetime import date as date_cls

    start = date_cls.fromisoformat(str(data["custom_start"]))
    scope = str(data.get("stats_scope") or "students")
    try:
        await _render_period_stats(
            message,
            session,
            state,
            scope=scope,
            kind=PeriodKind.CUSTOM,
            start_date=start,
            end_date=end,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()


@router.callback_query(F.data.startswith(StatsCB.CSV))
async def stats_csv(
    callback: CallbackQuery, db_user: User, session: Session, state: FSMContext
) -> None:
    if not _is_admin(db_user):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw = (callback.data or "").removeprefix(StatsCB.CSV)
    await callback.answer("Готовлю CSV…")
    if raw == "students30":
        rng = rolling_30d_range(session)
        payload = build_students_csv(session, date_range=rng)
        filename = "students_30d.csv"
    elif raw == "transfers30":
        rng = rolling_30d_range(session)
        payload = build_transfers_csv(session, date_range=rng)
        filename = "transfers_30d.csv"
    elif raw.startswith("teacher:"):
        # teacher:idx:period
        bits = raw.split(":")
        if len(bits) < 3:
            if callback.message:
                await callback.message.answer("Некорректный запрос CSV.")
            return
        idx = int(bits[1])
        period_raw = bits[2]
        data = await state.get_data()
        names = list(data.get("teacher_names") or list_teacher_names(session))
        if idx < 0 or idx >= len(names):
            if callback.message:
                await callback.message.answer("Преподаватель не найден.")
            return
        try:
            kind = PeriodKind(period_raw)
        except ValueError:
            kind = PeriodKind.DAYS_30
        if kind == PeriodKind.CUSTOM:
            kind = PeriodKind.DAYS_30
        rng = resolve_period(session, kind)
        payload = build_transfers_csv(
            session, date_range=rng, teacher_name=names[idx]
        )
        safe = "".join(c if c.isalnum() else "_" for c in names[idx])[:40]
        filename = f"teacher_{safe}_{kind.value}.csv"
    else:
        if callback.message:
            await callback.message.answer("Неизвестный тип выгрузки.")
        return

    if callback.message:
        await callback.message.answer_document(
            BufferedInputFile(payload, filename=filename),
            caption="Выгрузка CSV (разделитель `;`, открывается в Excel).",
        )
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu_keyboard(db_user),
        )
