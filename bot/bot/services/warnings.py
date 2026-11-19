"""Предупреждения стабильности §14: 2+/3+ переноса за 30 дней."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from bot.db.models import (
    Ticket,
    TicketInitiator,
    TicketType,
    User,
    UserRole,
)
from bot.services.analytics import (
    WARN_LEVEL_ADMIN,
    WARN_LEVEL_CURATOR,
    count_stability_transfers_for_student,
    list_admins,
)
from bot.services.duty import get_duty_curator
from bot.services.urgency import record_notification

NotifyLevel = Literal["curator", "curator_admin"]

KIND_STABILITY = "stability_warning"


@dataclass(frozen=True, slots=True)
class StabilityWarning:
    student_name: str
    student_external_id: str
    count_30d: int
    level: NotifyLevel
    ticket_id: int
    ticket_number: int
    message: str

    @property
    def antispam_key(self) -> str:
        return f"stability:{self.student_external_id}:ticket:{self.ticket_id}"


def _already_sent(session: Session, key: str) -> bool:
    from bot.db.models import NotificationLog

    row = (
        session.query(NotificationLog)
        .filter(
            NotificationLog.kind == KIND_STABILITY,
            NotificationLog.target_key == key,
        )
        .first()
    )
    return row is not None


def _students_from_ticket(ticket: Ticket) -> list[tuple[str, str]]:
    """Список (name, external_id) учеников, затронутых заявкой-переносом."""
    if ticket.ticket_type == TicketType.MASS:
        result: list[tuple[str, str]] = []
        for sub in ticket.subtasks or []:
            sid = (sub.student_external_id or "").strip()
            if sid:
                result.append((sub.student_name, sid))
        return result
    if ticket.ticket_type in (TicketType.ONE_TIME, TicketType.PERMANENT):
        sid = (ticket.student_external_id or "").strip()
        if sid:
            return [(ticket.student_name or "", sid)]
    return []


def format_warning_message(
    *,
    student_name: str,
    student_external_id: str,
    count_30d: int,
    ticket_number: int,
    teacher_name: str,
) -> str:
    level_label = (
        "⚠️ 2-й перенос за 30 дней"
        if count_30d == WARN_LEVEL_CURATOR
        else f"🚨 {count_30d} переноса за 30 дней"
    )
    _ = student_external_id  # ключ учёта внутри; в тексте бота не показываем
    return (
        f"{level_label}\n"
        f"Ученик: {student_name}\n"
        f"Преподаватель: {teacher_name}\n"
        f"Заявка: #{ticket_number}\n"
        f"Счётчик по инициативе преподавателя (без отказов/дублей/"
        f"родителя/школы): {count_30d} за скользящие 30 дней.\n"
        "Создание заявки не блокируется."
    )


def evaluate_stability_warnings(
    session: Session,
    ticket: Ticket,
) -> list[StabilityWarning]:
    """Оценить предупреждения при создании. Не блокирует. Без спама при повторе.

    Массовая заявка — отдельно по каждому ученику.
    Учитывается только инициатор «преподаватель».
    """
    if ticket.initiator != TicketInitiator.TEACHER:
        return []
    if ticket.ticket_type not in (
        TicketType.ONE_TIME,
        TicketType.PERMANENT,
        TicketType.MASS,
    ):
        return []

    warnings: list[StabilityWarning] = []
    for student_name, student_id in _students_from_ticket(ticket):
        count = count_stability_transfers_for_student(
            session, student_external_id=student_id
        )
        if count < WARN_LEVEL_CURATOR:
            continue
        level: NotifyLevel = (
            "curator_admin" if count >= WARN_LEVEL_ADMIN else "curator"
        )
        msg = format_warning_message(
            student_name=student_name,
            student_external_id=student_id,
            count_30d=count,
            ticket_number=ticket.number,
            teacher_name=ticket.teacher_name,
        )
        warning = StabilityWarning(
            student_name=student_name,
            student_external_id=student_id,
            count_30d=count,
            level=level,
            ticket_id=ticket.id,
            ticket_number=ticket.number,
            message=msg,
        )
        if _already_sent(session, warning.antispam_key):
            continue
        record_notification(
            session,
            kind=KIND_STABILITY,
            target_key=warning.antispam_key,
            details=f"count={count}; level={level}",
        )
        warnings.append(warning)
    return warnings


def recipients_for_warning(
    session: Session,
    ticket: Ticket,
    warning: StabilityWarning,
) -> list[User]:
    """Куратор (ответственный / дежурный); при 3+ — ещё админы."""
    seen: set[int] = set()
    result: list[User] = []

    def add(user: User | None) -> None:
        if user is None or not user.active:
            return
        if user.id in seen:
            return
        seen.add(user.id)
        result.append(user)

    assignee = ticket.assignee
    if assignee is not None and assignee.role in (
        UserRole.CURATOR,
        UserRole.ADMIN,
        UserRole.SENIOR_TEACHER,
    ):
        add(assignee)
    else:
        add(get_duty_curator(session))

    if warning.level == "curator_admin":
        for admin in list_admins(session):
            add(admin)
    return result


async def notify_stability_warnings(
    bot,
    session: Session,
    ticket: Ticket,
) -> list[StabilityWarning]:
    """Оценить, записать антиспам и разослать предупреждения. Не бросает наружу."""
    warnings = evaluate_stability_warnings(session, ticket)
    session.flush()
    for warning in warnings:
        for user in recipients_for_warning(session, ticket, warning):
            try:
                await bot.send_message(chat_id=user.telegram_id, text=warning.message)
            except Exception:
                # сеть/блокировка — не откатываем создание заявки
                pass
    return warnings


def dispatch_stability_warnings(session: Session, ticket: Ticket) -> list[StabilityWarning]:
    """Вызов из sync create_*: оценка + антиспам; рассылка через бота планировщика.

    Не трогает handlers tickets/mass — UX может править формы/карточки отдельно.
    """
    import asyncio
    import logging

    warnings = evaluate_stability_warnings(session, ticket)
    session.flush()
    if not warnings:
        return warnings

    # получателей фиксируем сразу — session может закрыться до async-отправки
    deliveries: list[tuple[int, str]] = []
    for warning in warnings:
        for user in recipients_for_warning(session, ticket, warning):
            deliveries.append((user.telegram_id, warning.message))

    try:
        from bot.services.scheduler import get_bot

        bot = get_bot()
    except Exception:
        bot = None
    if bot is None or not deliveries:
        return warnings

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return warnings

    log = logging.getLogger("avoperenos.warnings")
    ticket_number = ticket.number

    async def _send_all() -> None:
        for telegram_id, text in deliveries:
            try:
                await bot.send_message(chat_id=telegram_id, text=text)
            except Exception:
                log.exception(
                    "stability warn failed tg=%s ticket=%s",
                    telegram_id,
                    ticket_number,
                )

    loop.create_task(_send_all())
    return warnings
