"""Массовые переносы: общая заявка и независимые подзадачи по ученикам."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    CLOSED_SUBTASK_STATUSES,
    MASS_SUBTASK_ACTION_LABELS_RU,
    TICKET_INITIATOR_LABELS_RU,
    TICKET_STATUS_LABELS_RU,
    TICKET_TYPE_LABELS_RU,
    MassSubtaskAction,
    Ticket,
    TicketHistory,
    TicketInitiator,
    TicketStatus,
    TicketSubtask,
    TicketType,
    User,
    utcnow,
)
from bot.services.duty import resolve_assignee_for_new_ticket
from bot.services.tickets import (
    can_manage_ticket,
    next_ticket_number,
)
from bot.services.users import write_audit


@dataclass(frozen=True)
class MassProgress:
    total: int
    done: int
    waiting: int
    not_processed: int
    rejected: int

    @property
    def closed(self) -> int:
        return self.done + self.rejected

    @property
    def all_closed(self) -> bool:
        return self.total > 0 and self.closed == self.total

    def format_line(self) -> str:
        return (
            f"Прогресс: всего {self.total} / выполнено {self.done} / "
            f"ждут {self.waiting} / не обработано {self.not_processed}"
            + (f" / отказ {self.rejected}" if self.rejected else "")
        )


def compute_mass_progress(ticket: Ticket) -> MassProgress:
    subtasks = list(ticket.subtasks or [])
    done = sum(1 for s in subtasks if s.status == TicketStatus.DONE)
    waiting = sum(1 for s in subtasks if s.status == TicketStatus.WAITING_REPLY)
    not_processed = sum(
        1 for s in subtasks if s.status == TicketStatus.NOT_PROCESSED
    )
    rejected = sum(1 for s in subtasks if s.status == TicketStatus.REJECTED)
    return MassProgress(
        total=len(subtasks),
        done=done,
        waiting=waiting,
        not_processed=not_processed,
        rejected=rejected,
    )


def derive_mass_parent_status(progress: MassProgress) -> TicketStatus:
    """Общая заявка закрывается (done) только когда закрыты все подзадачи."""
    if progress.total == 0:
        return TicketStatus.NOT_PROCESSED
    if progress.all_closed:
        return TicketStatus.DONE
    if progress.waiting > 0:
        return TicketStatus.WAITING_REPLY
    return TicketStatus.NOT_PROCESSED


def sync_mass_ticket_status(
    session: Session,
    *,
    ticket: Ticket,
    actor: User | None = None,
) -> Ticket:
    """Пересчитать статус общей заявки по подзадачам."""
    if ticket.ticket_type != TicketType.MASS:
        return ticket
    progress = compute_mass_progress(ticket)
    new_status = derive_mass_parent_status(progress)
    if ticket.status == new_status:
        return ticket

    old_status = ticket.status
    ticket.status = new_status
    ticket.updated_at = utcnow()
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            subtask_id=None,
            actor_user_id=actor.id if actor else None,
            old_status=old_status,
            new_status=new_status,
            comment=f"Автосинхронизация по подзадачам. {progress.format_line()}",
        )
    )
    session.flush()
    return ticket


def create_mass_ticket(
    session: Session,
    *,
    creator: User,
    teacher_name: str,
    reason: str,
    absence_period: str,
    initiator: TicketInitiator,
    students: list[dict[str, Any]],
    comment: str | None = None,
    assignee: User | None = None,
) -> Ticket:
    """Создать массовую заявку с подзадачами по ученикам."""
    teacher = teacher_name.strip()
    reason_text = reason.strip()
    period = absence_period.strip()
    if not teacher:
        raise ValueError("Укажите преподавателя.")
    if not reason_text:
        raise ValueError("Укажите причину.")
    if not period:
        raise ValueError("Укажите период отсутствия.")
    if not students:
        raise ValueError("Добавьте хотя бы одного ученика.")
    if len(students) > 30:
        raise ValueError("Слишком много учеников в одной заявке (макс. 30).")

    parsed: list[dict[str, Any]] = []
    for idx, raw in enumerate(students, start=1):
        name = str(raw.get("student_name") or "").strip()
        # ID опционален (колонка остаётся для совместимости)
        sid = str(raw.get("student_external_id") or "").strip()
        lesson = str(raw.get("lesson_datetime") or "").strip()
        desired = str(raw.get("desired_new_datetime") or "").strip() or None
        action_raw = raw.get("required_action")
        stu_comment = str(raw.get("comment") or "").strip() or None
        if not name:
            raise ValueError(f"Ученик #{idx}: укажите имя.")
        if not lesson:
            raise ValueError(f"Ученик #{idx}: укажите даты/время занятий.")
        try:
            action = (
                action_raw
                if isinstance(action_raw, MassSubtaskAction)
                else MassSubtaskAction(str(action_raw))
            )
        except ValueError as exc:
            raise ValueError(f"Ученик #{idx}: неизвестное действие.") from exc
        parsed.append(
            {
                "student_name": name,
                "student_external_id": sid,
                "lesson_datetime": lesson,
                "desired_new_datetime": desired,
                "required_action": action,
                "comment": stu_comment,
            }
        )

    if assignee is None:
        assignee = resolve_assignee_for_new_ticket(session, creator)

    ticket = Ticket(
        number=next_ticket_number(session),
        ticket_type=TicketType.MASS,
        status=TicketStatus.NOT_PROCESSED,
        teacher_name=teacher,
        student_name=None,
        student_external_id=None,
        reason=reason_text,
        absence_period=period,
        initiator=initiator,
        comment=(comment or "").strip() or None,
        creator_id=creator.id,
        assignee_id=assignee.id if assignee else None,
    )
    session.add(ticket)
    session.flush()

    for item in parsed:
        sub = TicketSubtask(
            ticket_id=ticket.id,
            student_name=item["student_name"],
            student_external_id=item["student_external_id"],
            lesson_datetime=item["lesson_datetime"],
            desired_new_datetime=item["desired_new_datetime"],
            required_action=item["required_action"],
            status=TicketStatus.NOT_PROCESSED,
            comment=item["comment"],
        )
        session.add(sub)
    session.flush()

    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            actor_user_id=creator.id,
            old_status=None,
            new_status=TicketStatus.NOT_PROCESSED,
            comment=f"Создана массовая заявка, учеников: {len(parsed)}",
        )
    )
    session.flush()

    # Перезагрузить подзадачи и синхронизировать статус (останется not_processed)
    session.refresh(ticket)
    sync_mass_ticket_status(session, ticket=ticket, actor=creator)

    write_audit(
        session,
        actor_telegram_id=creator.telegram_id,
        action="mass_ticket_created",
        details=f"number={ticket.number}; students={len(parsed)}",
    )
    from bot.services.reminders import sync_ticket_reminders
    from bot.services.warnings import dispatch_stability_warnings

    sync_ticket_reminders(session, ticket)
    dispatch_stability_warnings(session, ticket)
    return ticket


def get_subtask_by_id(session: Session, subtask_id: int) -> TicketSubtask | None:
    return (
        session.query(TicketSubtask)
        .options(
            joinedload(TicketSubtask.ticket).joinedload(Ticket.creator),
            joinedload(TicketSubtask.ticket).joinedload(Ticket.assignee),
            joinedload(TicketSubtask.history),
        )
        .filter(TicketSubtask.id == subtask_id)
        .one_or_none()
    )


def change_subtask_status(
    session: Session,
    *,
    subtask: TicketSubtask,
    new_status: TicketStatus,
    actor: User,
    comment: str | None = None,
) -> TicketSubtask:
    ticket = subtask.ticket
    if ticket is None:
        ticket = session.get(Ticket, subtask.ticket_id)
    if ticket is None:
        raise ValueError("Родительская заявка не найдена.")
    if ticket.ticket_type != TicketType.MASS:
        raise ValueError("Подзадачи только у массовых заявок.")
    if not can_manage_ticket(actor, ticket):
        raise PermissionError("Недостаточно прав для изменения подзадачи.")

    old_status = subtask.status
    if old_status == new_status and not (comment or "").strip():
        return subtask

    subtask.status = new_status
    subtask.updated_at = utcnow()
    if comment and comment.strip():
        note = comment.strip()
        if subtask.comment:
            subtask.comment = f"{subtask.comment}\n— {note}"
        else:
            subtask.comment = note

    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            subtask_id=subtask.id,
            actor_user_id=actor.id,
            old_status=old_status,
            new_status=new_status,
            comment=(comment or "").strip() or None,
        )
    )
    session.flush()

    # Подгрузить все подзадачи для прогресса
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_mass_ticket_status(session, ticket=ticket, actor=actor)

    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="mass_subtask_status_changed",
        details=(
            f"number={ticket.number}; subtask={subtask.id}; "
            f"{old_status.value}->{new_status.value}"
        ),
    )
    from bot.services.reminders import sync_subtask_reminders, sync_ticket_reminders

    sync_subtask_reminders(session, subtask)
    sync_ticket_reminders(session, ticket)
    return subtask


def add_subtask_comment(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
    comment: str,
) -> tuple[TicketSubtask, list[User], str]:
    from bot.db.models import UserRole
    from bot.services.notify import can_comment_ticket, recipients_for_staff_comment
    from bot.services.tickets import (
        format_comment_notification,
        list_comment_notify_recipients,
    )

    text = comment.strip()
    if not text:
        raise ValueError("Комментарий пустой.")
    ticket = subtask.ticket or session.get(Ticket, subtask.ticket_id)
    if ticket is None:
        raise ValueError("Родительская заявка не найдена.")
    if not can_comment_ticket(actor, ticket):
        raise PermissionError("Недостаточно прав для комментария.")

    if subtask.comment:
        subtask.comment = f"{subtask.comment}\n— {text}"
    else:
        subtask.comment = text
    subtask.updated_at = utcnow()
    ticket.updated_at = utcnow()
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            subtask_id=subtask.id,
            actor_user_id=actor.id,
            old_status=subtask.status,
            new_status=subtask.status,
            comment=text,
        )
    )
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="mass_subtask_comment_added",
        details=f"number={ticket.number}; subtask={subtask.id}",
    )
    notify_text = format_comment_notification(
        ticket_number=ticket.number,
        actor_name=actor.name,
        comment=text,
        student_name=subtask.student_name,
    )
    if actor.role in (UserRole.SENIOR_TEACHER, UserRole.ADMIN):
        recipients = recipients_for_staff_comment(
            session, ticket=ticket, actor=actor
        )
    else:
        recipients = list_comment_notify_recipients(session, actor=actor)
    return subtask, recipients, notify_text


def format_mass_ticket_card(ticket: Ticket, *, with_history: bool = False) -> str:
    from bot.services.tickets import status_label_with_emoji

    progress = compute_mass_progress(ticket)
    status_label = status_label_with_emoji(ticket.status)

    lines = [
        f"🧾 <b>Заявка №{ticket.number}</b> — {TICKET_TYPE_LABELS_RU[TicketType.MASS]}",
        f"Статус: <b>{status_label}</b>",
        progress.format_line(),
        f"Преподаватель: {ticket.teacher_name}",
        f"Причина: {ticket.reason or '—'}",
        f"Период: {ticket.absence_period or '—'}",
    ]
    if ticket.comment:
        lines.append(f"📌 Заметка о замене / для куратора: {ticket.comment}")

    lines.append("")
    lines.append("<b>Ученики:</b>")
    for i, sub in enumerate(ticket.subtasks or [], start=1):
        action = MASS_SUBTASK_ACTION_LABELS_RU.get(
            sub.required_action, str(sub.required_action)
        )
        st = status_label_with_emoji(sub.status)
        lines.append(
            f"{i}. {sub.student_name} — {st}\n"
            f"   {sub.lesson_datetime} → {sub.desired_new_datetime or '—'}\n"
            f"   Действие: {action}"
        )

    if with_history:
        initiator_label = TICKET_INITIATOR_LABELS_RU[ticket.initiator]
        creator_name = ticket.creator.name if ticket.creator else "—"
        assignee_name = ticket.assignee.name if ticket.assignee else "не назначен"
        lines.extend(
            [
                "",
                f"Инициатор: {initiator_label}",
                f"Создал: {creator_name}",
                f"Куратор: {assignee_name}",
                f"Создана: {ticket.created_at.strftime('%d.%m.%Y %H:%M') if ticket.created_at else '—'}",
            ]
        )
        if ticket.history:
            lines.append("")
            lines.append("<b>История:</b>")
            for entry in ticket.history[-10:]:
                old = (
                    TICKET_STATUS_LABELS_RU[entry.old_status]
                    if entry.old_status
                    else "—"
                )
                new = (
                    TICKET_STATUS_LABELS_RU[entry.new_status]
                    if entry.new_status
                    else "—"
                )
                note = f" — {entry.comment}" if entry.comment else ""
                when = entry.created_at.strftime("%d.%m %H:%M") if entry.created_at else ""
                scope = f" [уч.{entry.subtask_id}]" if entry.subtask_id else ""
                lines.append(f"• {when}{scope}: {old} → {new}{note}")

    return "\n".join(lines)


def format_subtask_card(subtask: TicketSubtask) -> str:
    from bot.services.tickets import status_label_with_emoji

    ticket = subtask.ticket
    number = ticket.number if ticket else "?"
    action = MASS_SUBTASK_ACTION_LABELS_RU.get(
        subtask.required_action, str(subtask.required_action)
    )
    lines = [
        f"👤 <b>Подзадача</b> · заявка №{number}",
        f"Статус: <b>{status_label_with_emoji(subtask.status)}</b>",
        f"Ученик: {subtask.student_name}",
        f"Занятия: {subtask.lesson_datetime}",
        f"Желаемые новые даты: {subtask.desired_new_datetime or '—'}",
        f"Действие: {action}",
    ]
    if subtask.comment:
        lines.append(f"Комментарий: {subtask.comment}")
    if ticket:
        progress = compute_mass_progress(ticket)
        lines.append("")
        lines.append(progress.format_line())
        lines.append(f"Общая заявка: {status_label_with_emoji(ticket.status)}")
    return "\n".join(lines)


def format_mass_preview(data: dict[str, Any]) -> str:
    initiator = TicketInitiator(data["initiator"])
    students: list[dict[str, Any]] = list(data.get("students") or [])
    lines = [
        f"📋 <b>Превью: {TICKET_TYPE_LABELS_RU[TicketType.MASS]}</b>",
        f"Преподаватель: {data['teacher_name']}",
        f"Причина: {data['reason']}",
        f"Период отсутствия: {data['absence_period']}",
        f"Инициатор: {TICKET_INITIATOR_LABELS_RU[initiator]}",
    ]
    if data.get("comment"):
        lines.append(f"📌 Заметка о замене / для куратора: {data['comment']}")
    lines.append(f"Учеников: {len(students)}")
    lines.append("")
    for i, stu in enumerate(students, start=1):
        action = MassSubtaskAction(stu["required_action"])
        lines.append(
            f"{i}. {stu['student_name']}\n"
            f"   {stu['lesson_datetime']} → {stu.get('desired_new_datetime') or '—'}\n"
            f"   {MASS_SUBTASK_ACTION_LABELS_RU[action]}"
            + (f"\n   ✎ {stu['comment']}" if stu.get("comment") else "")
        )
    return "\n".join(lines)


def is_subtask_closed(subtask: TicketSubtask) -> bool:
    return subtask.status in CLOSED_SUBTASK_STATUSES
