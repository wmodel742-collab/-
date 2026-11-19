"""Клавиатуры подбора замены и карточек подзадач."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import (
    CLOSED_REPLACEMENT_STAGES,
    ReplacementStage,
    Ticket,
    TicketSubtask,
    TicketType,
)
from bot.keyboards.tickets import TicketCB


class ReplCB:
    """Префиксы callback_data для подбора замены."""

    PREFIX = "repl:"
    KIND = "repl:kind:"
    NAV = "repl:nav:"
    SKIP = "repl:skip:"
    STUDENT = "repl:stu:"
    PREVIEW = "repl:prev:"
    OPEN_SUB = "repl:sub:"
    SUB_ACTION = "repl:sact:"
    BACK_TICKET = "repl:btkt:"


def repl_cancel_back_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{ReplCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ReplCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def repl_kind_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Увольнение",
                    callback_data=f"{ReplCB.KIND}{TicketType.DISMISSAL.value}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Временное отсутствие",
                    callback_data=f"{ReplCB.KIND}{TicketType.TEMPORARY_ABSENCE.value}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{ReplCB.NAV}cancel",
                )
            ],
        ]
    )


def repl_comment_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"{ReplCB.SKIP}comment",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{ReplCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ReplCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def repl_student_comment_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"{ReplCB.SKIP}stu_comment",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{ReplCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{ReplCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def repl_after_student_keyboard(*, students_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ Добавить ещё ученика",
                callback_data=f"{ReplCB.STUDENT}add",
            )
        ],
    ]
    if students_count >= 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ К превью",
                    callback_data=f"{ReplCB.STUDENT}done",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"{ReplCB.NAV}back",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"{ReplCB.NAV}cancel",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def repl_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=f"{ReplCB.PREVIEW}create",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Добавить ещё ученика",
                    callback_data=f"{ReplCB.STUDENT}add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить шапку",
                    callback_data=f"{ReplCB.PREVIEW}edit",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{ReplCB.NAV}cancel",
                ),
            ],
        ]
    )


def replacement_ticket_card_keyboard(
    ticket: Ticket, *, detailed: bool = False
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for sub in (ticket.subtasks or [])[:20]:
        stage = sub.replacement_stage or ReplacementStage.SEARCHING
        icon = {
            ReplacementStage.SEARCHING: "🔍",
            ReplacementStage.FOUND: "👤",
            ReplacementStage.OFFERED: "✉️",
            ReplacementStage.AGREED: "✅",
            ReplacementStage.CLOSED_NO_CONTINUE: "⏹",
        }.get(stage, "·")
        help_mark = "🆘" if sub.needs_senior_help else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon}{help_mark} {sub.student_name}",
                    callback_data=f"{ReplCB.OPEN_SUB}{sub.id}",
                )
            ]
        )
    footer: list[InlineKeyboardButton] = []
    if not detailed:
        footer.append(
            InlineKeyboardButton(
                text="ℹ️ Подробнее",
                callback_data=f"{TicketCB.DETAILS}{ticket.id}",
            )
        )
    footer.append(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"{TicketCB.OPEN}{ticket.id}",
        )
    )
    rows.append(footer)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def replacement_subtask_card_keyboard(subtask: TicketSubtask) -> InlineKeyboardMarkup:
    sid = subtask.id
    tid = subtask.ticket_id
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    rows: list[list[InlineKeyboardButton]] = []

    if stage not in CLOSED_REPLACEMENT_STAGES:
        if stage == ReplacementStage.SEARCHING:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="👤 Кандидат найден",
                        callback_data=f"{ReplCB.SUB_ACTION}found:{sid}",
                    )
                ]
            )
        if stage in (ReplacementStage.FOUND, ReplacementStage.OFFERED):
            if stage == ReplacementStage.FOUND:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="✉️ Написали родителю",
                            callback_data=f"{ReplCB.SUB_ACTION}offered:{sid}",
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Выполнено",
                        callback_data=f"{ReplCB.SUB_ACTION}done:{sid}",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔄 Искать другого",
                        callback_data=f"{ReplCB.SUB_ACTION}another:{sid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🆘 Нужна помощь старшего",
                    callback_data=f"{ReplCB.SUB_ACTION}help:{sid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏹ Ученик не продолжает",
                    callback_data=f"{ReplCB.SUB_ACTION}nocont:{sid}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=f"{ReplCB.OPEN_SUB}{sid}",
            ),
            InlineKeyboardButton(
                text="◀️ К заявке",
                callback_data=f"{ReplCB.BACK_TICKET}{tid}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
