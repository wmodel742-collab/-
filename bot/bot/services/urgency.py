"""Срочность заявок и антиспам уведомлений."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Iterable

from sqlalchemy.orm import Session

from bot.db.models import (
    CLOSED_REPLACEMENT_STAGES,
    CLOSED_SUBTASK_STATUSES,
    NotificationLog,
    REPLACEMENT_TICKET_TYPES,
    ReplacementStage,
    Ticket,
    TicketStatus,
    TicketSubtask,
    TicketType,
    utcnow,
)
from bot.services.automation_settings import load_automation_config
from bot.services import schedule as schedule_service
from bot.services.tickets import ACTIVE_STATUSES
from bot.services.worktime import is_within_work_hours


@dataclass(frozen=True, slots=True)
class OpenTicketStats:
    total: int
    urgent: int
    waiting: int
    not_processed: int


def parse_lesson_moment(
    raw: str | date | datetime | None,
    *,
    tz,
    default_time: time | None = None,
) -> datetime | None:
    """Гибкий разбор даты/времени занятия из полей заявки."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=tz)
        return raw.astimezone(tz)
    if isinstance(raw, date) and not isinstance(raw, datetime):
        hhmm = default_time or time(12, 0)
        return datetime(raw.year, raw.month, raw.day, hhmm.hour, hhmm.minute, tzinfo=tz)

    text = str(raw).strip()
    if not text:
        return None

    formats = (
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                hhmm = default_time or time(12, 0)
                dt = dt.replace(hour=hhmm.hour, minute=hhmm.minute)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def transfer_deadline(ticket: Ticket, *, tz) -> datetime | None:
    if ticket.ticket_type == TicketType.ONE_TIME:
        return parse_lesson_moment(ticket.original_datetime, tz=tz) or parse_lesson_moment(
            ticket.new_datetime, tz=tz
        )
    if ticket.ticket_type == TicketType.PERMANENT:
        return parse_lesson_moment(ticket.nearest_lesson_date, tz=tz)
    return None


def replacement_nearest_deadline(ticket: Ticket, *, tz) -> datetime | None:
    moments: list[datetime] = []
    for sub in ticket.subtasks or []:
        if sub.replacement_stage in CLOSED_REPLACEMENT_STAGES:
            continue
        if sub.status in CLOSED_SUBTASK_STATUSES:
            continue
        m = parse_lesson_moment(sub.lesson_datetime, tz=tz)
        if m is not None:
            moments.append(m)
    if not moments:
        return None
    return min(moments)


def ticket_is_urgent(session: Session, ticket: Ticket, *, now: datetime | None = None) -> bool:
    if ticket.status not in ACTIVE_STATUSES:
        return False
    cfg = load_automation_config(session)
    tz = schedule_service.get_school_tz(session)
    moment = now or schedule_service.school_now(session)

    if ticket.ticket_type in (TicketType.ONE_TIME, TicketType.PERMANENT):
        deadline = transfer_deadline(ticket, tz=tz)
        if deadline is None:
            return False
        delta = deadline - moment
        return timedelta(0) <= delta < timedelta(hours=cfg.urgency_transfer_hours)

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        # не согласована = есть открытые подзадачи не в AGREED
        open_subs = [
            s
            for s in (ticket.subtasks or [])
            if s.replacement_stage not in CLOSED_REPLACEMENT_STAGES
            and s.status not in CLOSED_SUBTASK_STATUSES
        ]
        if not open_subs:
            return False
        deadline = replacement_nearest_deadline(ticket, tz=tz)
        if deadline is None:
            return False
        delta = deadline - moment
        return timedelta(0) <= delta < timedelta(hours=cfg.urgency_replacement_hours)

    # mass: срочность по ближайшему занятию среди открытых подзадач
    if ticket.ticket_type == TicketType.MASS:
        moments: list[datetime] = []
        for sub in ticket.subtasks or []:
            if sub.status in CLOSED_SUBTASK_STATUSES:
                continue
            m = parse_lesson_moment(sub.lesson_datetime, tz=tz)
            if m is not None:
                moments.append(m)
        if not moments:
            return False
        deadline = min(moments)
        delta = deadline - moment
        return timedelta(0) <= delta < timedelta(hours=cfg.urgency_transfer_hours)

    return False


def collect_open_ticket_stats(
    session: Session,
    *,
    assignee_id: int | None = None,
) -> OpenTicketStats:
    q = session.query(Ticket).filter(Ticket.status.in_(ACTIVE_STATUSES))
    if assignee_id is not None:
        q = q.filter(Ticket.assignee_id == assignee_id)
    tickets = q.all()
    # подгрузить subtasks для срочности
    for t in tickets:
        _ = t.subtasks
    urgent = sum(1 for t in tickets if ticket_is_urgent(session, t))
    waiting = sum(1 for t in tickets if t.status == TicketStatus.WAITING_REPLY)
    not_processed = sum(1 for t in tickets if t.status == TicketStatus.NOT_PROCESSED)
    return OpenTicketStats(
        total=len(tickets),
        urgent=urgent,
        waiting=waiting,
        not_processed=not_processed,
    )


def list_urgent_tickets(session: Session, *, now: datetime | None = None) -> list[Ticket]:
    tickets = (
        session.query(Ticket)
        .filter(Ticket.status.in_(ACTIVE_STATUSES))
        .order_by(Ticket.number.asc())
        .all()
    )
    for t in tickets:
        _ = t.subtasks
    return [t for t in tickets if ticket_is_urgent(session, t, now=now)]


def _last_notification(
    session: Session, *, kind: str, target_key: str
) -> NotificationLog | None:
    return (
        session.query(NotificationLog)
        .filter(
            NotificationLog.kind == kind,
            NotificationLog.target_key == target_key,
        )
        .order_by(NotificationLog.created_at.desc())
        .first()
    )


def can_send_urgency(
    session: Session,
    *,
    target_key: str,
    now: datetime | None = None,
) -> bool:
    cfg = load_automation_config(session)
    moment = now or utcnow()
    work_start, work_end = schedule_service.get_work_hours(session)
    local = moment
    if local.tzinfo is None:
        local = schedule_service.school_now(session)
    else:
        local = local.astimezone(schedule_service.get_school_tz(session))

    if not is_within_work_hours(local, work_start, work_end) and not cfg.notify_off_hours:
        return False

    last = _last_notification(session, kind="urgency", target_key=target_key)
    if last is None:
        return True
    last_at = last.created_at
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=moment.tzinfo)
    return (moment - last_at) >= timedelta(minutes=cfg.urgency_antispam_minutes)


def record_notification(
    session: Session,
    *,
    kind: str,
    target_key: str,
    details: str | None = None,
) -> NotificationLog:
    row = NotificationLog(kind=kind, target_key=target_key, details=details)
    session.add(row)
    session.flush()
    return row


def urgency_target_key(ticket: Ticket) -> str:
    return f"ticket:{ticket.id}"


def format_urgency_message(ticket: Ticket) -> str:
    return (
        f"🚨 Срочная заявка #{ticket.number}\n"
        f"Тип: {ticket.ticket_type.value}\n"
        f"Преподаватель: {ticket.teacher_name}\n"
        f"Статус: {ticket.status.value}"
    )
