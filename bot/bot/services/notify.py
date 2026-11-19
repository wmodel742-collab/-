"""Уведомления при создании заявок и комментариях (дежурный / кураторы)."""

from __future__ import annotations

import logging
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.orm import Session

from bot.db.models import (
    TICKET_TYPE_LABELS_RU,
    Ticket,
    TicketType,
    User,
    UserRole,
)
from bot.services.duty import get_duty_curator

logger = logging.getLogger("avoperenos.notify")


def format_new_ticket_duty_notification(ticket: Ticket, *, creator: User) -> str:
    """Краткое ЛС дежурному / кураторам о новой заявке."""
    type_label = TICKET_TYPE_LABELS_RU.get(ticket.ticket_type, ticket.ticket_type.value)
    lines = [
        f"🆕 <b>Новая заявка №{ticket.number}</b> — {escape(type_label)}",
        f"Создал: {escape(creator.name)}",
        f"Преподаватель: {escape(ticket.teacher_name)}",
    ]
    if ticket.ticket_type == TicketType.MASS:
        n = len(ticket.subtasks or [])
        lines.append(f"Учеников: {n}")
        if ticket.reason:
            lines.append(f"Причина: {escape(ticket.reason)}")
        if ticket.absence_period:
            lines.append(f"Период: {escape(ticket.absence_period)}")
    elif ticket.student_name:
        lines.append(f"Ученик: {escape(ticket.student_name)}")

    if ticket.assignee is not None:
        lines.append(f"Ответственный: {escape(ticket.assignee.name)}")

    if ticket.comment:
        lines.append(
            f"📌 Заметка о замене / для куратора: {escape(ticket.comment)}"
        )

    lines.append("")
    lines.append("Откройте «Активные заявки» или кнопку ниже.")
    return "\n".join(lines)


def open_ticket_notify_keyboard(ticket: Ticket) -> InlineKeyboardMarkup:
    from bot.keyboards.tickets import TicketCB

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📋 Открыть №{ticket.number}",
                    callback_data=f"{TicketCB.OPEN}{ticket.id}",
                )
            ]
        ]
    )


def list_active_curators(session: Session) -> list[User]:
    return (
        session.query(User)
        .filter(User.role == UserRole.CURATOR, User.active.is_(True))
        .order_by(User.id.asc())
        .all()
    )


def recipients_for_new_ticket_duty(
    session: Session, *, ticket: Ticket, creator: User
) -> list[User]:
    """Кому слать о новой заявке.

    Всегда: текущий дежурный + все активные кураторы + ответственный.
    Автора заявки не дублируем (если создал куратор сам себе — другим кураторам всё равно шлём).
    """
    seen: set[int] = set()
    result: list[User] = []

    def add(user: User | None) -> None:
        if user is None or not user.active:
            return
        if user.id == creator.id:
            return
        if user.id in seen:
            return
        seen.add(user.id)
        result.append(user)

    add(get_duty_curator(session))
    for curator in list_active_curators(session):
        add(curator)
    add(ticket.assignee)
    return result


def recipients_for_staff_comment(
    session: Session,
    *,
    ticket: Ticket,
    actor: User,
) -> list[User]:
    """Кому слать комментарий старшего/админа: ответственный + дежурный + кураторы + админы/старшие."""
    from bot.services.tickets import list_comment_notify_recipients

    seen: set[int] = set()
    result: list[User] = []

    def add(user: User | None) -> None:
        if user is None or not user.active:
            return
        if user.id == actor.id:
            return
        if user.id in seen:
            return
        seen.add(user.id)
        result.append(user)

    add(ticket.assignee)
    add(get_duty_curator(session))
    for curator in list_active_curators(session):
        add(curator)
    for user in list_comment_notify_recipients(session, actor=actor):
        add(user)
    return result


async def send_telegram_messages(
    bot,
    recipients: list[User],
    text: str,
    *,
    reply_markup=None,
) -> int:
    """Разослать текст; при ошибке HTML пробуем без parse_mode."""
    notified = 0
    for user in recipients:
        try:
            await bot.send_message(
                user.telegram_id,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            notified += 1
        except Exception:
            logger.exception(
                "notify HTML failed tg=%s — retry plain", user.telegram_id
            )
            try:
                await bot.send_message(
                    user.telegram_id,
                    text.replace("<b>", "")
                    .replace("</b>", "")
                    .replace("<i>", "")
                    .replace("</i>", ""),
                    reply_markup=reply_markup,
                )
                notified += 1
            except Exception:
                logger.exception("notify plain failed tg=%s", user.telegram_id)
    return notified


async def notify_new_ticket(
    bot,
    session: Session,
    *,
    ticket: Ticket,
    creator: User,
) -> str:
    """Уведомить дежурного и всех кураторов (+ рабочий чат). Статус для создателя."""
    # подтянуть assignee для текста
    if ticket.assignee_id and ticket.assignee is None:
        ticket = session.get(Ticket, ticket.id) or ticket

    text = format_new_ticket_duty_notification(ticket, creator=creator)
    recipients = recipients_for_new_ticket_duty(
        session, ticket=ticket, creator=creator
    )
    markup = open_ticket_notify_keyboard(ticket)
    notified = await send_telegram_messages(
        bot, recipients, text, reply_markup=markup
    )

    chat_ok = False
    try:
        from bot.services.automation_settings import load_automation_config

        cfg = load_automation_config(session)
        if cfg.work_chat_id:
            await bot.send_message(
                cfg.work_chat_id, text, parse_mode="HTML", reply_markup=markup
            )
            chat_ok = True
    except Exception:
        logger.exception("work chat notify failed ticket=%s", ticket.number)

    if not recipients:
        return (
            "⚠️ Нет получателей: добавьте активного куратора в «Сотрудники» "
            "и проверьте график дежурств"
        )
    if notified:
        names = ", ".join(u.name for u in recipients[:5])
        more = f" (+{len(recipients) - 5})" if len(recipients) > 5 else ""
        extra = " + рабочий чат" if chat_ok else ""
        return f"🔔 Уведомлены кураторы: {names}{more}{extra}"
    if chat_ok:
        return "🔔 Уведомление отправлено в рабочий чат (ЛС кураторам не дошло)"
    return (
        "⚠️ Кураторы не уведомлены — проверьте, что они нажали /start боту "
        "и Telegram ID в карточке сотрудника верный"
    )


def can_comment_ticket(user: User, ticket: Ticket) -> bool:
    """Комментарий / заметка о замене: управление + старшие по разовым/массовым."""
    from bot.services.tickets import can_manage_ticket

    if can_manage_ticket(user, ticket):
        return True
    if user.role == UserRole.SENIOR_TEACHER and ticket.ticket_type in (
        TicketType.ONE_TIME,
        TicketType.PERMANENT,
        TicketType.MASS,
    ):
        return True
    return False
