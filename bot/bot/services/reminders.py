"""Планирование напоминаний по заявкам и подзадачам замены."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from bot.db.models import (
    CLOSED_REPLACEMENT_STAGES,
    CLOSED_SUBTASK_STATUSES,
    REPLACEMENT_TICKET_TYPES,
    ReplacementStage,
    Ticket,
    TicketStatus,
    TicketSubtask,
    TicketType,
)
from bot.services.automation_settings import load_automation_config
from bot.services.jobs import (
    get_job_gateway,
    subtask_reminder_job_id,
    ticket_reminder_job_id,
)
from bot.services import schedule as schedule_service
from bot.services.tickets import ACTIVE_STATUSES
from bot.services.worktime import add_work_hours

logger = logging.getLogger("avoperenos.reminders")

CALLBACK_TICKET = "reminder_ticket"
CALLBACK_SUBTASK = "reminder_subtask"


def _work_context(session: Session):
    cfg = load_automation_config(session)
    start, end = schedule_service.get_work_hours(session)
    now = schedule_service.school_now(session)
    return cfg, start, end, now


def compute_next_reminder_at(
    session: Session,
    *,
    hours: float,
    from_moment: datetime | None = None,
    is_repeat: bool = False,
) -> datetime:
    """Следующий момент напоминания в рабочих часах (с переносом остатка)."""
    cfg, start, end, now = _work_context(session)
    _ = cfg
    moment = from_moment or now
    return add_work_hours(moment, hours, start, end)


def cancel_ticket_reminder(ticket_id: int) -> None:
    get_job_gateway().remove_job(ticket_reminder_job_id(ticket_id))


def cancel_subtask_reminder(subtask_id: int) -> None:
    get_job_gateway().remove_job(subtask_reminder_job_id(subtask_id))


def schedule_ticket_reminder(
    session: Session,
    ticket: Ticket,
    *,
    hours: float,
    from_moment: datetime | None = None,
) -> datetime | None:
    if ticket.status not in ACTIVE_STATUSES:
        cancel_ticket_reminder(ticket.id)
        return None
    if ticket.ticket_type not in (TicketType.ONE_TIME, TicketType.PERMANENT):
        # массовые/замены — на уровне подзадач
        cancel_ticket_reminder(ticket.id)
        return None
    if ticket.status != TicketStatus.WAITING_REPLY:
        cancel_ticket_reminder(ticket.id)
        return None

    run_at = compute_next_reminder_at(session, hours=hours, from_moment=from_moment)
    job_id = ticket_reminder_job_id(ticket.id)
    get_job_gateway().schedule_date(
        job_id,
        run_at,
        CALLBACK_TICKET,
        ticket_id=ticket.id,
    )
    return run_at


def schedule_subtask_reminder(
    session: Session,
    subtask: TicketSubtask,
    *,
    hours: float,
    from_moment: datetime | None = None,
) -> datetime | None:
    ticket = subtask.ticket
    if ticket is None:
        return None

    # закрыто — стоп
    if subtask.status in CLOSED_SUBTASK_STATUSES:
        cancel_subtask_reminder(subtask.id)
        return None
    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        stage = subtask.replacement_stage or ReplacementStage.SEARCHING
        if stage in CLOSED_REPLACEMENT_STAGES:
            cancel_subtask_reminder(subtask.id)
            return None

    run_at = compute_next_reminder_at(session, hours=hours, from_moment=from_moment)
    job_id = subtask_reminder_job_id(subtask.id)
    get_job_gateway().schedule_date(
        job_id,
        run_at,
        CALLBACK_SUBTASK,
        subtask_id=subtask.id,
    )
    return run_at


def sync_ticket_reminders(session: Session, ticket: Ticket) -> None:
    """Вызывать после создания / смены статуса заявки."""
    cfg = load_automation_config(session)

    if ticket.status in (TicketStatus.DONE, TicketStatus.REJECTED):
        cancel_ticket_reminder(ticket.id)
        for sub in ticket.subtasks or []:
            cancel_subtask_reminder(sub.id)
        return

    if ticket.ticket_type in (TicketType.ONE_TIME, TicketType.PERMANENT):
        if ticket.status == TicketStatus.WAITING_REPLY:
            # первое напоминание через waiting_first; если job уже есть — не сбрасываем
            # при первом входе в статус планируем заново
            gw = get_job_gateway()
            job_id = ticket_reminder_job_id(ticket.id)
            if not gw.has_job(job_id):
                schedule_ticket_reminder(
                    session, ticket, hours=cfg.waiting_first_hours
                )
        else:
            cancel_ticket_reminder(ticket.id)
        return

    # mass / replacement — синхронизируем подзадачи
    cancel_ticket_reminder(ticket.id)
    for sub in ticket.subtasks or []:
        sync_subtask_reminders(session, sub)


def sync_subtask_reminders(
    session: Session, subtask: TicketSubtask, *, force: bool = False
) -> None:
    cfg = load_automation_config(session)
    ticket = subtask.ticket
    if ticket is None:
        cancel_subtask_reminder(subtask.id)
        return

    if subtask.status in CLOSED_SUBTASK_STATUSES:
        cancel_subtask_reminder(subtask.id)
        return

    gw = get_job_gateway()
    job_id = subtask_reminder_job_id(subtask.id)

    if ticket.ticket_type == TicketType.MASS:
        if subtask.status == TicketStatus.WAITING_REPLY:
            if force or not gw.has_job(job_id):
                schedule_subtask_reminder(
                    session, subtask, hours=cfg.waiting_first_hours
                )
        else:
            cancel_subtask_reminder(subtask.id)
        return

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        stage = subtask.replacement_stage or ReplacementStage.SEARCHING
        if stage in CLOSED_REPLACEMENT_STAGES:
            cancel_subtask_reminder(subtask.id)
            return
        if stage == ReplacementStage.SEARCHING:
            hours = cfg.searching_hours
        elif stage == ReplacementStage.FOUND:
            hours = cfg.found_hours
        elif stage == ReplacementStage.OFFERED:
            hours = cfg.waiting_first_hours
        else:
            cancel_subtask_reminder(subtask.id)
            return
        # force=True при смене этапа — сброс интервала; иначе без дублей/сброса
        if force or not gw.has_job(job_id):
            schedule_subtask_reminder(session, subtask, hours=hours)
        return

    cancel_subtask_reminder(subtask.id)


def reschedule_after_fire_ticket(session: Session, ticket: Ticket) -> datetime | None:
    """После срабатывания напоминания — повтор каждые N раб.часов, если ещё ждём."""
    cfg = load_automation_config(session)
    if ticket.status != TicketStatus.WAITING_REPLY:
        cancel_ticket_reminder(ticket.id)
        return None
    return schedule_ticket_reminder(
        session, ticket, hours=cfg.waiting_repeat_hours
    )


def reschedule_after_fire_subtask(
    session: Session, subtask: TicketSubtask
) -> datetime | None:
    cfg = load_automation_config(session)
    ticket = subtask.ticket
    if ticket is None or subtask.status in CLOSED_SUBTASK_STATUSES:
        cancel_subtask_reminder(subtask.id)
        return None

    if ticket.ticket_type == TicketType.MASS:
        if subtask.status != TicketStatus.WAITING_REPLY:
            cancel_subtask_reminder(subtask.id)
            return None
        return schedule_subtask_reminder(
            session, subtask, hours=cfg.waiting_repeat_hours
        )

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        stage = subtask.replacement_stage or ReplacementStage.SEARCHING
        if stage in CLOSED_REPLACEMENT_STAGES:
            cancel_subtask_reminder(subtask.id)
            return None
        if stage == ReplacementStage.SEARCHING:
            hours = cfg.searching_hours
        elif stage == ReplacementStage.FOUND:
            hours = cfg.found_hours
        elif stage == ReplacementStage.OFFERED:
            hours = cfg.waiting_repeat_hours
        else:
            cancel_subtask_reminder(subtask.id)
            return None
        return schedule_subtask_reminder(session, subtask, hours=hours)

    cancel_subtask_reminder(subtask.id)
    return None


def format_ticket_reminder_text(ticket: Ticket) -> str:
    return (
        f"⏰ Напоминание по заявке #{ticket.number}\n"
        f"Статус: ждём ответа\n"
        f"Преподаватель: {ticket.teacher_name}\n"
        f"Ученик: {ticket.student_name or '—'}"
    )


def format_subtask_reminder_text(subtask: TicketSubtask) -> str:
    ticket = subtask.ticket
    number = ticket.number if ticket else "?"
    stage = ""
    if subtask.replacement_stage:
        stage = f"\nЭтап: {subtask.replacement_stage.value}"
    return (
        f"⏰ Напоминание по заявке #{number}\n"
        f"Ученик: {subtask.student_name}"
        f"{stage}\n"
        f"Статус: {subtask.status.value}"
    )
