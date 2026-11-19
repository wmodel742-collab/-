"""Клавиатуры календаря и управления графиком кураторов."""

from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import User
from bot.services.schedule import DutyInfo, shift_month, short_name


class SchedCB:
    """Префиксы callback_data для графика (лимит Telegram 64 байта)."""

    PREFIX = "sch:"
    MONTH = "sch:m:"  # sch:m:YYYY-MM:v|m
    DAY = "sch:d:"  # sch:d:YYYY-MM-DD:v|m
    SET = "sch:set:"  # sch:set:YYYY-MM-DD:userId
    RST = "sch:rst:"  # sch:rst:YYYY-MM-DD
    SWAP = "sch:sw:"  # sch:sw:start | sch:sw:YYYY-MM-DD
    CYC = "sch:cyc:"  # sch:cyc:start | sch:cyc:YYYY-MM-DD | sch:cyc:u:userId
    BACK = "sch:back:"  # sch:back:YYYY-MM:v|m
    TODAY = "sch:td:"  # sch:td:v|m
    HELP = "sch:help"
    CANCEL = "sch:x"


def _mode_flag(manage: bool) -> str:
    return "m" if manage else "v"


def month_nav_keyboard(
    year: int,
    month: int,
    *,
    manage: bool = False,
) -> InlineKeyboardMarkup:
    flag = _mode_flag(manage)
    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"{SchedCB.MONTH}{prev_y:04d}-{prev_m:02d}:{flag}",
            ),
            InlineKeyboardButton(
                text="Сегодня",
                callback_data=f"{SchedCB.TODAY}{flag}",
            ),
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"{SchedCB.MONTH}{next_y:04d}-{next_m:02d}:{flag}",
            ),
        ]
    ]
    if manage:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❓ Как пользоваться",
                    callback_data=f"{SchedCB.HELP}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Поменять смены местами",
                    callback_data=f"{SchedCB.SWAP}start",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📐 Новый цикл 2/2",
                    callback_data=f"{SchedCB.CYC}start",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ В меню",
                callback_data="menu:home",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def month_days_keyboard(
    year: int,
    month: int,
    duties: dict[date, DutyInfo],
    *,
    manage: bool = False,
    pick_mode: str | None = None,
) -> InlineKeyboardMarkup:
    """Сетка дней месяца. pick_mode: None | 'swap' | 'cycle'."""
    flag = _mode_flag(manage)
    rows: list[list[InlineKeyboardButton]] = []
    header = [
        InlineKeyboardButton(text=d, callback_data=SchedCB.CANCEL)
        for d in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    ]
    rows.append(header)

    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day.month != month:
                row.append(InlineKeyboardButton(text=" ", callback_data=SchedCB.CANCEL))
                continue
            info = duties.get(day)
            label = f"{day.day}"
            if info is not None:
                mark = short_name(info.curator)
                label = f"{day.day}{mark}"
                if info.from_override:
                    label += "*"
            if pick_mode == "swap":
                cb = f"{SchedCB.SWAP}{day.isoformat()}"
            elif pick_mode == "cycle":
                cb = f"{SchedCB.CYC}{day.isoformat()}"
            elif manage:
                cb = f"{SchedCB.DAY}{day.isoformat()}:{flag}"
            else:
                cb = f"{SchedCB.DAY}{day.isoformat()}:{flag}"
            row.append(InlineKeyboardButton(text=label, callback_data=cb))
        rows.append(row)

    rows.extend(month_nav_keyboard(year, month, manage=manage).inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_manage_keyboard(on_date: date, curators: list[User]) -> InlineKeyboardMarkup:
    iso = on_date.isoformat()
    rows: list[list[InlineKeyboardButton]] = []
    for curator in curators:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Дежурит: {curator.name}",
                    callback_data=f"{SchedCB.SET}{iso}:{curator.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="↩ Как по графику 2/2",
                callback_data=f"{SchedCB.RST}{iso}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад к календарю",
                callback_data=f"{SchedCB.BACK}{on_date.year:04d}-{on_date.month:02d}:m",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cycle_first_curator_keyboard(
    on_date: date, curators: list[User]
) -> InlineKeyboardMarkup:
    """Выбор первого куратора нового цикла (второй — оставшийся из пары / следующий)."""
    rows: list[list[InlineKeyboardButton]] = []
    for curator in curators:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Первые 2 дня: {curator.name}",
                    callback_data=f"{SchedCB.CYC}u:{on_date.isoformat()}:{curator.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"{SchedCB.BACK}{on_date.year:04d}-{on_date.month:02d}:m",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_schedule_keyboard(year: int, month: int, *, manage: bool) -> InlineKeyboardMarkup:
    flag = _mode_flag(manage)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{SchedCB.BACK}{year:04d}-{month:02d}:{flag}",
                )
            ]
        ]
    )
