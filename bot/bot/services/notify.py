"""Уведомления при создании заявок и комментариях (дежурный / ответственный)."""

from __future__ import annotations

import logging

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
    """Краткое ЛС дежурному о новой заявке."""
    type_label = TICKET_TYPE_LABELS_RU.get(ticket.ticket_type, ticket.ticket_type.value)
    lines = [
        f"🆕 <b>Новая заявка №{ticket.number}</b> — {type_label}",
        f"Создал: {creator.name}",
        f"Преподаватель: {ticket.teacher_name}",
    ]
    if ticket.ticket_type == TicketType.MASS:
        n = len(ticket.subtasks or [])
        lines.append(f"Учеников: {n}")
        if ticket.reason:
            lines.append(f"Причина: {ticket.reason}")
        if ticket.absence_period:
            lines.append(f"Период: {ticket.absence_period}")
    elif ticket.student_name:
        lines.append(f"Ученик: {ticket.student_name}")

    if ticket.comment:
        lines.append(f"📌 Заметка о замене / для куратора: {ticket.comment}")

    lines.append("")
    lines.append("Откройте «Активные заявки» или поиск по номеру.")
    return "\n".join(lines)


def recipients_for_new_ticket_duty(
    session: Session, *, ticket: Ticket, creator: User
) -> list[User]:
    """Кому слать о новой заявке: дежурный, иначе другие кураторы; + ответственный.

    Автора заявки не дублируем.
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

    duty = get_duty_curator(session)
    add(duty)

    # Если дежурный = автор или не найден — остальные активные кураторы
    if not result:
        others = (
            session.query(User)
            .filter(
                User.role == UserRole.CURATOR,
                User.active.is_(True),
                User.id != creator.id,
            )
            .order_by(User.id.asc())
            .all()
        )
        for user in others:
            add(user)

    add(ticket.assignee)
    return result


def recipients_for_staff_comment(
    session: Session,
    *,
    ticket: Ticket,
    actor: User,
) -> list[User]:
    """Кому слать комментарий старшего/админа: ответственный + дежурный + админы/старшие."""
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
    for user in list_comment_notify_recipients(session, actor=actor):
        add(user)
    return result


async def send_telegram_messages(bot, recipients: list[User], text: str) -> int:
    """Разослать текст; ошибки сети не пробрасываем. Возвращает число успешных."""
    notified = 0
    for user in recipients:
        try:
            await bot.send_message(user.telegram_id, text, parse_mode="HTML")
            notified += 1
        except Exception:
            logger.exception("notify failed tg=%s", user.telegram_id)
    return notified


async def notify_new_ticket(
    bot,
    session: Session,
    *,
    ticket: Ticket,
    creator: User,
) -> str:
    """Уведомить дежурного (+ fallback / рабочий чат). Вернуть строку статуса для создателя."""
    text = format_new_ticket_duty_notification(ticket, creator=creator)
    recipients = recipients_for_new_ticket_duty(
        session, ticket=ticket, creator=creator
    )
    notified = await send_telegram_messages(bot, recipients, text)

    chat_ok = False
    try:
        from bot.services.automation_settings import get_automation_config

        cfg = get_automation_config(session)
        if cfg.work_chat_id:
            await bot.send_message(cfg.work_chat_id, text, parse_mode="HTML")
            chat_ok = True
    except Exception:
        logger.exception("work chat notify failed ticket=%s", ticket.number)

    if notified:
        names = ", ".join(u.name for u in recipients[:3])
        extra = " + рабочий чат" if chat_ok else ""
        return f"🔔 Дежурный уведомлён: {names}{extra}"
    if chat_ok:
        return "🔔 Уведомление отправлено в рабочий чат"
    return (
        "⚠️ Дежурный не уведомлён — проверьте график кураторов "
        "и Telegram ID сотрудников"
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
