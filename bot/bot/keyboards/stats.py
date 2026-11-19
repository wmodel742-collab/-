"""Клавиатуры раздела «Статистика» (админ)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.analytics import PeriodKind, PERIOD_LABELS_RU


class StatsCB:
    PREFIX = "sts:"
    ROOT = "sts:root"
    TEACHERS = "sts:teachers"
    STUDENTS = "sts:students"
    PERIOD = "sts:period:"
    TEACHER = "sts:teacher:"
    TEACHER_PAGE = "sts:tpage:"
    CSV = "sts:csv:"
    WEEKLY = "sts:weekly"
    CUSTOM = "sts:custom"
    BACK = "sts:back"


def stats_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👩‍🏫 По преподавателям",
                    callback_data=StatsCB.TEACHERS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎓 По ученикам",
                    callback_data=StatsCB.STUDENTS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Еженедельный отчёт",
                    callback_data=StatsCB.WEEKLY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 CSV: ученики (30 дн.)",
                    callback_data=f"{StatsCB.CSV}students30",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 CSV: переносы (30 дн.)",
                    callback_data=f"{StatsCB.CSV}transfers30",
                )
            ],
        ]
    )


def period_keyboard(*, scope: str) -> InlineKeyboardMarkup:
    """scope: teachers | students | teacher:<name_b64 or index> — храним в callback коротко."""
    rows: list[list[InlineKeyboardButton]] = []
    for kind in (
        PeriodKind.DAYS_7,
        PeriodKind.DAYS_30,
        PeriodKind.CALENDAR_MONTH,
        PeriodKind.CUSTOM,
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=PERIOD_LABELS_RU[kind],
                    callback_data=f"{StatsCB.PERIOD}{scope}:{kind.value}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="◀️ Назад", callback_data=StatsCB.ROOT)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def teachers_list_keyboard(
    names: list[str], *, page: int = 0, page_size: int = 8
) -> InlineKeyboardMarkup:
    total = len(names)
    start = page * page_size
    chunk = names[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    for idx, name in enumerate(chunk, start=start):
        label = name if len(name) <= 40 else name[:37] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{StatsCB.TEACHER}{idx}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=f"{StatsCB.TEACHER_PAGE}{page - 1}",
            )
        )
    if start + page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=f"{StatsCB.TEACHER_PAGE}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text="◀️ К статистике", callback_data=StatsCB.ROOT)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def teacher_actions_keyboard(*, teacher_idx: int, period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 CSV по преподавателю",
                    callback_data=f"{StatsCB.CSV}teacher:{teacher_idx}:{period}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ К списку",
                    callback_data=StatsCB.TEACHERS,
                )
            ],
            [InlineKeyboardButton(text="◀️ К статистике", callback_data=StatsCB.ROOT)],
        ]
    )


def weekly_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Подробная статистика",
                    callback_data=StatsCB.ROOT,
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 CSV ученики 30 дн.",
                    callback_data=f"{StatsCB.CSV}students30",
                )
            ],
        ]
    )


def custom_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=StatsCB.ROOT)]
        ]
    )
