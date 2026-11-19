"""Клавиатуры создания и карточки заявки на перенос."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import Ticket, TicketStatus, WEEKDAY_LABELS_RU


class TicketCB:
    """Префиксы callback_data для заявок."""

    PREFIX = "tkt:"
    TYPE = "tkt:type:"
    INIT = "tkt:init:"
    STATE = "tkt:state:"
    NAV = "tkt:nav:"
    PREVIEW = "tkt:prev:"
    ACTION = "tkt:act:"
    OPEN = "tkt:open:"
    DETAILS = "tkt:det:"
    SKIP = "tkt:skip:"
    WEEKDAY = "tkt:wd:"


def cancel_back_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{TicketCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{TicketCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transfer_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разовый",
                    callback_data=f"{TicketCB.TYPE}one_time",
                ),
                InlineKeyboardButton(
                    text="Постоянный",
                    callback_data=f"{TicketCB.TYPE}permanent",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{TicketCB.NAV}cancel",
                )
            ],
        ]
    )


def initiator_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Преподаватель",
                callback_data=f"{TicketCB.INIT}teacher",
            )
        ],
        [
            InlineKeyboardButton(
                text="Ученик / родитель",
                callback_data=f"{TicketCB.INIT}student_parent",
            )
        ],
        [
            InlineKeyboardButton(
                text="Школа",
                callback_data=f"{TicketCB.INIT}school",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{TicketCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{TicketCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def initial_state_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Ещё не написали",
                callback_data=f"{TicketCB.STATE}not_written",
            )
        ],
        [
            InlineKeyboardButton(
                text="Уже написали",
                callback_data=f"{TicketCB.STATE}written",
            )
        ],
        [
            InlineKeyboardButton(
                text="Уже выполнено",
                callback_data=f"{TicketCB.STATE}done",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{TicketCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{TicketCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comment_keyboard(*, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"{TicketCB.SKIP}comment",
            )
        ],
    ]
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{TicketCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{TicketCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def weekday_keyboard(*, field: str, show_back: bool = True) -> InlineKeyboardMarkup:
    """field: current | new — чтобы различать шаг."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for num, label in WEEKDAY_LABELS_RU.items():
        row.append(
            InlineKeyboardButton(
                text=label[:2],
                callback_data=f"{TicketCB.WEEKDAY}{field}:{num}",
            )
        )
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav: list[InlineKeyboardButton] = []
    if show_back:
        nav.append(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"{TicketCB.NAV}back")
        )
    nav.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"{TicketCB.NAV}cancel")
    )
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=f"{TicketCB.PREVIEW}create",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"{TicketCB.PREVIEW}edit",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{TicketCB.NAV}cancel",
                ),
            ],
        ]
    )


def ticket_card_keyboard(
    ticket: Ticket, *, detailed: bool = False
) -> InlineKeyboardMarkup:
    """Кнопки действий по карточке. Не авто-закрываем в «Выполнено»."""
    tid = ticket.id
    rows: list[list[InlineKeyboardButton]] = []

    if ticket.status != TicketStatus.DONE:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✉️ Написали ученику",
                    callback_data=f"{TicketCB.ACTION}wrote:{tid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Выполнено",
                    callback_data=f"{TicketCB.ACTION}done:{tid}",
                ),
                InlineKeyboardButton(
                    text="❌ Отказ",
                    callback_data=f"{TicketCB.ACTION}reject:{tid}",
                ),
            ]
        )
        if ticket.status == TicketStatus.WAITING_REPLY:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответа пока нет",
                        callback_data=f"{TicketCB.ACTION}no_reply:{tid}",
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔄 Предложить другое время",
                        callback_data=f"{TicketCB.ACTION}other_time:{tid}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="📝 Заметка о замене",
                    callback_data=f"{TicketCB.ACTION}comment:{tid}",
                )
            ]
        )

    detail_row: list[InlineKeyboardButton] = []
    if not detailed:
        detail_row.append(
            InlineKeyboardButton(
                text="ℹ️ Подробнее",
                callback_data=f"{TicketCB.DETAILS}{tid}",
            )
        )
    detail_row.append(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"{TicketCB.OPEN}{tid}",
        )
    )
    rows.append(detail_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tickets_list_keyboard(tickets: list[Ticket]) -> InlineKeyboardMarkup:
    from bot.services.tickets import TICKET_STATUS_EMOJI, group_tickets_by_status

    rows: list[list[InlineKeyboardButton]] = []
    ordered: list[Ticket] = []
    for _status, group in group_tickets_by_status(tickets):
        ordered.extend(group)
    for ticket in ordered[:20]:
        emoji = TICKET_STATUS_EMOJI.get(ticket.status, "·")
        tval = ticket.ticket_type.value
        if tval == "mass":
            label = f"{emoji} №{ticket.number} · масс · {ticket.teacher_name}"
        elif tval in ("dismissal", "temporary_absence"):
            short = "увол" if tval == "dismissal" else "отсут"
            label = f"{emoji} №{ticket.number} · {short} · {ticket.teacher_name}"
        else:
            label = f"{emoji} №{ticket.number} · {ticket.student_name}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=f"{TicketCB.OPEN}{ticket.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
