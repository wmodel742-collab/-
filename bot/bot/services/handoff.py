"""Смена дежурства: переназначение незакрытых задач новому куратору."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    TICKET_STATUS_LABELS_RU,
    Ticket,
    TicketHistory,
    TicketStatus,
    User,
    utcnow,
)
from bot.services.duty import get_duty_curator
from bot.services import schedule as schedule_service
from bot.services.tickets import ACTIVE_STATUSES
from bot.services.users import write_audit
from bot.services.urgency import collect_open_ticket_stats, ticket_is_urgent


@dataclass(frozen=True, slots=True)
class HandoffResult:
    previous: User | None
    current: User | None
    reassigned: int
    changed: bool
    reason: str


def list_open_tickets(session: Session) -> list[Ticket]:
    return (
        session.query(Ticket)
        .options(joinedload(Ticket.assignee), joinedload(Ticket.subtasks))
        .filter(Ticket.status.in_(ACTIVE_STATUSES))
        .order_by(Ticket.number.asc())
        .all()
    )


def reassign_open_tickets(
    session: Session,
    *,
    new_assignee: User,
    reason: str,
    actor_telegram_id: int | None = None,
) -> int:
    """Назначить все незакрытые заявки новому ответственному. Статусы сохраняются."""
    tickets = list_open_tickets(session)
    count = 0
    for ticket in tickets:
        old_id = ticket.assignee_id
        if old_id == new_assignee.id:
            continue
        old_name = ticket.assignee.name if ticket.assignee else "—"
        ticket.assignee_id = new_assignee.id
        ticket.updated_at = utcnow()
        session.add(
            TicketHistory(
                ticket_id=ticket.id,
                actor_user_id=None,
                old_status=ticket.status,
                new_status=ticket.status,
                comment=(
                    f"Смена дежурства ({reason}): "
                    f"{old_name} → {new_assignee.name}"
                ),
            )
        )
        count += 1
    if count:
        session.flush()
        write_audit(
            session,
            actor_telegram_id=actor_telegram_id,
            action="duty_handoff_reassigned",
            details=f"to={new_assignee.id}; count={count}; reason={reason}",
        )
    return count


def run_morning_duty_handoff(
    session: Session,
    *,
    on_date: date | None = None,
) -> HandoffResult:
    """Ежедневная смена: переназначение только если дежурный сменился относительно вчера."""
    today = on_date or schedule_service.school_today(session)
    yesterday = today - timedelta(days=1)
    current = get_duty_curator(session, today)
    previous = get_duty_curator(session, yesterday)

    if current is None:
        return HandoffResult(
            previous=previous,
            current=None,
            reassigned=0,
            changed=False,
            reason="no_duty",
        )

    changed = previous is None or previous.id != current.id
    reassigned = 0
    if changed:
        reassigned = reassign_open_tickets(
            session,
            new_assignee=current,
            reason="morning_handoff",
        )
    return HandoffResult(
        previous=previous,
        current=current,
        reassigned=reassigned,
        changed=changed,
        reason="morning_handoff" if changed else "same_duty",
    )


def immediate_duty_handoff_if_today(
    session: Session,
    *,
    on_date: date,
    actor_telegram_id: int | None = None,
) -> HandoffResult | None:
    """Ручная смена дежурного админом на «сегодня» школы → переназначение сразу."""
    today = schedule_service.school_today(session)
    if on_date != today:
        return None
    current = get_duty_curator(session, today)
    if current is None:
        return HandoffResult(
            previous=None,
            current=None,
            reassigned=0,
            changed=False,
            reason="manual_no_duty",
        )
    reassigned = reassign_open_tickets(
        session,
        new_assignee=current,
        reason="manual_duty_change",
        actor_telegram_id=actor_telegram_id,
    )
    return HandoffResult(
        previous=None,
        current=current,
        reassigned=reassigned,
        changed=True,
        reason="manual_duty_change",
    )


def format_morning_message(
    session: Session,
    *,
    duty: User,
    handoff: HandoffResult,
) -> str:
    stats = collect_open_ticket_stats(session, assignee_id=duty.id)
    lines = [
        f"Доброе утро, {duty.name.split()[0]}! 🥑",
        "",
    ]
    if handoff.changed and handoff.reassigned:
        prev = handoff.previous.name if handoff.previous else "—"
        lines.append(
            f"Смена дежурства: {prev} → {duty.name}. "
            f"Переназначено заявок: {handoff.reassigned}."
        )
        lines.append("")
    elif handoff.changed:
        lines.append("Сегодня ваша смена. Открытых заявок для переназначения не было.")
        lines.append("")
    else:
        lines.append("Дежурство продолжается — задачи остаются за вами.")
        lines.append("")

    lines.extend(
        [
            f"Всего незакрытых: {stats.total}",
            f"• Срочные: {stats.urgent}",
            f"• {TICKET_STATUS_LABELS_RU[TicketStatus.WAITING_REPLY]}: {stats.waiting}",
            f"• {TICKET_STATUS_LABELS_RU[TicketStatus.NOT_PROCESSED]}: {stats.not_processed}",
        ]
    )
    return "\n".join(lines)


def format_evening_digest(session: Session, *, duty: User) -> str:
    tickets = [
        t
        for t in list_open_tickets(session)
        if t.assignee_id == duty.id
    ]
    lines = [
        f"Сводка на вечер, {duty.name.split()[0]}",
        f"Незавершённых заявок: {len(tickets)}",
        "",
    ]
    if not tickets:
        lines.append("Все задачи закрыты — отличный день!")
        return "\n".join(lines)

    for t in tickets[:25]:
        urgent = "🚨 " if ticket_is_urgent(session, t) else ""
        st = TICKET_STATUS_LABELS_RU.get(t.status, t.status.value)
        lines.append(f"{urgent}#{t.number} · {st} · {t.teacher_name}")
    if len(tickets) > 25:
        lines.append(f"… и ещё {len(tickets) - 25}")
    lines.append("")
    lines.append(
        "После 21:00 обычные напоминания переносятся на следующий рабочий день."
    )
    return "\n".join(lines)
