"""Клавиатуры массового переноса и карточек подзадач."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import (
    MASS_SUBTASK_ACTION_LABELS_RU,
    Ticket,
    TicketStatus,
    TicketSubtask,
)
from bot.keyboards.tickets import TicketCB


class MassCB:
    """Префиксы callback_data для массовых заявок."""

    PREFIX = "mass:"
    NAV = "mass:nav:"
    INIT = "mass:init:"
    SKIP = "mass:skip:"
    ACTION = "mass:act:"
    STUDENT = "mass:stu:"
    PREVIEW = "mass:prev:"
    OPEN_SUB = "mass:sub:"
    SUB_ACTION = "mass:sact:"
    BACK_TICKET = "mass:btkt:"


def mass_cancel_back_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{MassCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{MassCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_initiator_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Преподаватель",
                callback_data=f"{MassCB.INIT}teacher",
            )
        ],
        [
            InlineKeyboardButton(
                text="Ученик / родитель",
                callback_data=f"{MassCB.INIT}student_parent",
            )
        ],
        [
            InlineKeyboardButton(
                text="Школа",
                callback_data=f"{MassCB.INIT}school",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{MassCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{MassCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_comment_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"{MassCB.SKIP}comment",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{MassCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{MassCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_required_action_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for action, label in MASS_SUBTASK_ACTION_LABELS_RU.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{MassCB.ACTION}{action.value}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{MassCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{MassCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_student_comment_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"{MassCB.SKIP}stu_comment",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{MassCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{MassCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_after_student_keyboard(*, students_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ Добавить ещё ученика",
                callback_data=f"{MassCB.STUDENT}add",
            )
        ],
    ]
    if students_count >= 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ К превью",
                    callback_data=f"{MassCB.STUDENT}done",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"{MassCB.NAV}back",
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"{MassCB.NAV}cancel",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=f"{MassCB.PREVIEW}create",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ Добавить ещё ученика",
                    callback_data=f"{MassCB.STUDENT}add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить шапку",
                    callback_data=f"{MassCB.PREVIEW}edit",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{MassCB.NAV}cancel",
                ),
            ],
        ]
    )


def mass_ticket_card_keyboard(
    ticket: Ticket, *, detailed: bool = False
) -> InlineKeyboardMarkup:
    """Карточка массовой заявки: список подзадач, без прямого закрытия шапки."""
    rows: list[list[InlineKeyboardButton]] = []
    for sub in (ticket.subtasks or [])[:20]:
        st = {
            TicketStatus.NOT_PROCESSED: "⏳",
            TicketStatus.WAITING_REPLY: "💬",
            TicketStatus.DONE: "✅",
            TicketStatus.REJECTED: "❌",
        }.get(sub.status, "·")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{st} {sub.student_name}",
                    callback_data=f"{MassCB.OPEN_SUB}{sub.id}",
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


def subtask_card_keyboard(subtask: TicketSubtask) -> InlineKeyboardMarkup:
    sid = subtask.id
    tid = subtask.ticket_id
    rows: list[list[InlineKeyboardButton]] = []

    if subtask.status != TicketStatus.DONE:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✉️ Написали ученику",
                    callback_data=f"{MassCB.SUB_ACTION}wrote:{sid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Выполнено",
                    callback_data=f"{MassCB.SUB_ACTION}done:{sid}",
                ),
                InlineKeyboardButton(
                    text="❌ Отказ",
                    callback_data=f"{MassCB.SUB_ACTION}reject:{sid}",
                ),
            ]
        )
        if subtask.status == TicketStatus.WAITING_REPLY:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответа пока нет",
                        callback_data=f"{MassCB.SUB_ACTION}no_reply:{sid}",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔄 Предложить другое время",
                        callback_data=f"{MassCB.SUB_ACTION}other_time:{sid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📝 Комментарий",
                    callback_data=f"{MassCB.SUB_ACTION}comment:{sid}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=f"{MassCB.OPEN_SUB}{sid}",
            ),
            InlineKeyboardButton(
                text="◀️ К заявке",
                callback_data=f"{MassCB.BACK_TICKET}{tid}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
