"""Клавиатуры календаря и управления графиком дежурного."""

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
    SWAP = "sch:sw:"  # sch:sw:start | sch:sw:YYYY-MM-DD | sch:sw:ok:A:B
    CYC = "sch:cyc:"  # sch:cyc:start | date | u: | u2: | ok:...
    BACK = "sch:back:"  # sch:back:YYYY-MM:v|m
    TODAY = "sch:td:"  # sch:td:v|m
    HELP = "sch:help"
    HUB = "sch:hub"
    CAL = "sch:cal"  # открыть календарь из хаба
    CANCEL = "sch:x"
    ABORT = "sch:abort"  # отмена пошагового действия → хаб


def _mode_flag(manage: bool) -> str:
    return "m" if manage else "v"


def manage_hub_keyboard() -> InlineKeyboardMarkup:
    """Главное меню настройки графика дежурного (для админа)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Открыть календарь",
                    callback_data=SchedCB.CAL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Назначить дежурного на день",
                    callback_data=f"{SchedCB.DAY}pick",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Поменять два дня местами",
                    callback_data=f"{SchedCB.SWAP}start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📐 Задать цикл 2/2 с даты",
                    callback_data=f"{SchedCB.CYC}start",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Как это работает",
                    callback_data=SchedCB.HELP,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ В меню",
                    callback_data="menu:home",
                )
            ],
        ]
    )


def month_nav_keyboard(
    year: int,
    month: int,
    *,
    manage: bool = False,
    pick_mode: str | None = None,
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
    if manage and pick_mode:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отменить действие",
                    callback_data=SchedCB.ABORT,
                )
            ]
        )
    elif manage:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🏠 К действиям",
                    callback_data=SchedCB.HUB,
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="👤 Назначить на день",
                    callback_data=f"{SchedCB.DAY}pick",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Поменять два дня",
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
                    text="❓ Справка",
                    callback_data=SchedCB.HELP,
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
    today: date | None = None,
) -> InlineKeyboardMarkup:
    """Сетка дней месяца. pick_mode: None | 'swap' | 'cycle' | 'day'."""
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
                row.append(InlineKeyboardButton(text="·", callback_data=SchedCB.CANCEL))
                continue
            info = duties.get(day)
            label = f"{day.day}"
            if info is not None:
                mark = short_name(info.curator)
                label = f"{day.day}{mark}"
                if info.from_override:
                    label += "*"
            if today is not None and day == today:
                label = f"[{label}]"
            if pick_mode == "swap":
                cb = f"{SchedCB.SWAP}{day.isoformat()}"
            elif pick_mode == "cycle":
                cb = f"{SchedCB.CYC}{day.isoformat()}"
            elif pick_mode == "day":
                cb = f"{SchedCB.DAY}{day.isoformat()}:{flag}"
            elif manage:
                cb = f"{SchedCB.DAY}{day.isoformat()}:{flag}"
            else:
                cb = f"{SchedCB.DAY}{day.isoformat()}:{flag}"
            row.append(InlineKeyboardButton(text=label, callback_data=cb))
        rows.append(row)

    rows.extend(
        month_nav_keyboard(
            year, month, manage=manage, pick_mode=pick_mode
        ).inline_keyboard
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_manage_keyboard(
    on_date: date,
    curators: list[User],
    *,
    current_id: int | None = None,
    has_override: bool = False,
) -> InlineKeyboardMarkup:
    iso = on_date.isoformat()
    rows: list[list[InlineKeyboardButton]] = []
    for curator in curators:
        if current_id is not None and curator.id == current_id:
            text = f"● Сейчас дежурит: {curator.name}"
        else:
            text = f"Назначить: {curator.name}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"{SchedCB.SET}{iso}:{curator.id}",
                )
            ]
        )
    if has_override:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩ Убрать подмену (как по циклу 2/2)",
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
    rows.append(
        [
            InlineKeyboardButton(
                text="🏠 К действиям",
                callback_data=SchedCB.HUB,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cycle_first_curator_keyboard(
    on_date: date, curators: list[User]
) -> InlineKeyboardMarkup:
    """Выбор первого куратора нового цикла."""
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
                callback_data=SchedCB.ABORT,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cycle_second_curator_keyboard(
    on_date: date, first: User, others: list[User]
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for curator in others:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Следующие 2 дня: {curator.name}",
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
                callback_data=SchedCB.ABORT,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_swap_keyboard(day_a: date, day_b: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить обмен",
                    callback_data=(
                        f"{SchedCB.SWAP}ok:{day_a.isoformat()}:{day_b.isoformat()}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=SchedCB.ABORT,
                )
            ],
        ]
    )


def confirm_cycle_keyboard(
    on_date: date, first_id: int, second_id: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить новый цикл",
                    callback_data=(
                        f"{SchedCB.CYC}ok:{on_date.isoformat()}:{first_id}:{second_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=SchedCB.ABORT,
                )
            ],
        ]
    )


def cancel_schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=SchedCB.ABORT,
                )
            ]
        ]
    )
