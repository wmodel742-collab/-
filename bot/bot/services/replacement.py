"""Подбор замены: увольнение / временное отсутствие, этапы, история кандидатов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    CLOSED_REPLACEMENT_STAGES,
    CLOSED_SUBTASK_STATUSES,
    PROPOSAL_OUTCOME_LABELS_RU,
    REPLACEMENT_STAGE_LABELS_RU,
    REPLACEMENT_STAGE_TO_STATUS,
    REPLACEMENT_TICKET_TYPES,
    TICKET_STATUS_LABELS_RU,
    TICKET_TYPE_LABELS_RU,
    ProposalOutcome,
    ReplacementProposal,
    ReplacementStage,
    Ticket,
    TicketHistory,
    TicketInitiator,
    TicketStatus,
    TicketSubtask,
    TicketType,
    User,
    UserRole,
    utcnow,
)
from bot.services.duty import resolve_assignee_for_new_ticket
from bot.services.tickets import can_manage_ticket, next_ticket_number, parse_date_ru
from bot.services.users import write_audit


@dataclass(frozen=True)
class ReplacementProgress:
    total: int
    searching: int
    found: int
    offered: int
    agreed: int
    closed_no_continue: int
    needs_help: int

    @property
    def closed(self) -> int:
        return self.agreed + self.closed_no_continue

    @property
    def all_closed(self) -> bool:
        return self.total > 0 and self.closed == self.total

    def format_line(self) -> str:
        parts = [
            f"Прогресс: всего {self.total}",
            f"ищем {self.searching}",
            f"найден {self.found}",
            f"предложено {self.offered}",
            f"согласовано {self.agreed}",
        ]
        if self.closed_no_continue:
            parts.append(f"не продолжает {self.closed_no_continue}")
        if self.needs_help:
            parts.append(f"помощь старшего {self.needs_help}")
        return " / ".join(parts)


def is_replacement_ticket(ticket: Ticket) -> bool:
    return ticket.ticket_type in REPLACEMENT_TICKET_TYPES


def compute_replacement_progress(ticket: Ticket) -> ReplacementProgress:
    subtasks = list(ticket.subtasks or [])
    counts = {stage: 0 for stage in ReplacementStage}
    needs_help = 0
    for sub in subtasks:
        stage = sub.replacement_stage or ReplacementStage.SEARCHING
        counts[stage] = counts.get(stage, 0) + 1
        if sub.needs_senior_help and stage not in CLOSED_REPLACEMENT_STAGES:
            needs_help += 1
    return ReplacementProgress(
        total=len(subtasks),
        searching=counts[ReplacementStage.SEARCHING],
        found=counts[ReplacementStage.FOUND],
        offered=counts[ReplacementStage.OFFERED],
        agreed=counts[ReplacementStage.AGREED],
        closed_no_continue=counts[ReplacementStage.CLOSED_NO_CONTINUE],
        needs_help=needs_help,
    )


def derive_replacement_parent_status(progress: ReplacementProgress) -> TicketStatus:
    if progress.total == 0:
        return TicketStatus.NOT_PROCESSED
    if progress.all_closed:
        return TicketStatus.DONE
    if progress.offered > 0:
        return TicketStatus.WAITING_REPLY
    return TicketStatus.NOT_PROCESSED


def sync_replacement_ticket_status(
    session: Session,
    *,
    ticket: Ticket,
    actor: User | None = None,
) -> Ticket:
    if not is_replacement_ticket(ticket):
        return ticket
    progress = compute_replacement_progress(ticket)
    new_status = derive_replacement_parent_status(progress)
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
            comment=f"Автосинхронизация по подзадачам замены. {progress.format_line()}",
        )
    )
    session.flush()
    return ticket


def _parse_students(students: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not students:
        raise ValueError("Добавьте хотя бы одного ученика.")
    if len(students) > 30:
        raise ValueError("Слишком много учеников в одной заявке (макс. 30).")

    parsed: list[dict[str, Any]] = []
    for idx, raw in enumerate(students, start=1):
        name = str(raw.get("student_name") or "").strip()
        # ID опционален (колонка остаётся для совместимости)
        sid = str(raw.get("student_external_id") or "").strip()
        subject = str(raw.get("subject") or "").strip()
        schedule = str(raw.get("schedule") or raw.get("lesson_datetime") or "").strip()
        stu_comment = str(raw.get("comment") or "").strip() or None
        if not name:
            raise ValueError(f"Ученик #{idx}: укажите имя.")
        if not subject:
            raise ValueError(f"Ученик #{idx}: укажите предмет.")
        if not schedule:
            raise ValueError(f"Ученик #{idx}: укажите расписание.")
        parsed.append(
            {
                "student_name": name,
                "student_external_id": sid,
                "subject": subject,
                "lesson_datetime": schedule,
                "comment": stu_comment,
            }
        )
    return parsed


def create_replacement_ticket(
    session: Session,
    *,
    creator: User,
    kind: TicketType | str,
    teacher_name: str,
    absence_start_date: date | str,
    students: list[dict[str, Any]],
    return_date: date | str | None = None,
    comment: str | None = None,
    assignee: User | None = None,
) -> Ticket:
    """Создать заявку на подбор замены с подзадачами по ученикам."""
    if isinstance(kind, str):
        kind = TicketType(kind)
    if kind not in REPLACEMENT_TICKET_TYPES:
        raise ValueError("Тип заявки: dismissal или temporary_absence.")

    teacher = teacher_name.strip()
    if not teacher:
        raise ValueError("Укажите ФИО преподавателя.")

    start = (
        absence_start_date
        if isinstance(absence_start_date, date)
        else parse_date_ru(str(absence_start_date))
    )
    ret: date | None = None
    if kind == TicketType.TEMPORARY_ABSENCE:
        if return_date is None or (isinstance(return_date, str) and not return_date.strip()):
            raise ValueError("Для временного отсутствия укажите дату возвращения.")
        ret = (
            return_date
            if isinstance(return_date, date)
            else parse_date_ru(str(return_date))
        )
        if ret < start:
            raise ValueError("Дата возвращения не может быть раньше начала отсутствия.")
    elif return_date is not None and str(return_date).strip():
        ret = (
            return_date
            if isinstance(return_date, date)
            else parse_date_ru(str(return_date))
        )

    parsed = _parse_students(students)

    if assignee is None:
        assignee = resolve_assignee_for_new_ticket(session, creator)

    ticket = Ticket(
        number=next_ticket_number(session),
        ticket_type=kind,
        status=TicketStatus.NOT_PROCESSED,
        teacher_name=teacher,
        student_name=None,
        student_external_id=None,
        absence_start_date=start,
        return_date=ret,
        initiator=TicketInitiator.SCHOOL,
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
            subject=item["subject"],
            required_action=None,
            replacement_stage=ReplacementStage.SEARCHING,
            current_candidate_name=None,
            needs_senior_help=False,
            status=TicketStatus.NOT_PROCESSED,
            comment=item["comment"],
        )
        session.add(sub)
    session.flush()

    kind_label = TICKET_TYPE_LABELS_RU[kind]
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            actor_user_id=creator.id,
            old_status=None,
            new_status=TicketStatus.NOT_PROCESSED,
            comment=f"Создана заявка «{kind_label}», учеников: {len(parsed)}",
        )
    )
    session.flush()
    session.refresh(ticket)
    sync_replacement_ticket_status(session, ticket=ticket, actor=creator)

    write_audit(
        session,
        actor_telegram_id=creator.telegram_id,
        action="replacement_ticket_created",
        details=f"number={ticket.number}; type={kind.value}; students={len(parsed)}",
    )
    from bot.services.reminders import sync_ticket_reminders

    session.refresh(ticket)
    _ = ticket.subtasks
    sync_ticket_reminders(session, ticket)
    return ticket


def get_replacement_subtask(session: Session, subtask_id: int) -> TicketSubtask | None:
    return (
        session.query(TicketSubtask)
        .options(
            joinedload(TicketSubtask.ticket).joinedload(Ticket.creator),
            joinedload(TicketSubtask.ticket).joinedload(Ticket.assignee),
            joinedload(TicketSubtask.history),
            joinedload(TicketSubtask.proposals),
        )
        .filter(TicketSubtask.id == subtask_id)
        .one_or_none()
    )


def _require_open_replacement_subtask(
    session: Session, subtask: TicketSubtask, actor: User
) -> Ticket:
    ticket = subtask.ticket or session.get(Ticket, subtask.ticket_id)
    if ticket is None:
        raise ValueError("Родительская заявка не найдена.")
    if not is_replacement_ticket(ticket):
        raise ValueError("Подзадачи замены только у заявок dismissal / temporary_absence.")
    if not can_manage_ticket(actor, ticket):
        raise PermissionError("Недостаточно прав для изменения подзадачи.")
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    if stage in CLOSED_REPLACEMENT_STAGES:
        raise ValueError("Подзадача уже закрыта.")
    return ticket


def _set_stage(
    session: Session,
    *,
    subtask: TicketSubtask,
    ticket: Ticket,
    actor: User,
    new_stage: ReplacementStage,
    comment: str | None = None,
) -> None:
    old_stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    old_status = subtask.status
    new_status = REPLACEMENT_STAGE_TO_STATUS[new_stage]
    subtask.replacement_stage = new_stage
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
            comment=(
                f"{REPLACEMENT_STAGE_LABELS_RU[old_stage]} → "
                f"{REPLACEMENT_STAGE_LABELS_RU[new_stage]}"
                + (f". {comment.strip()}" if comment and comment.strip() else "")
            ),
        )
    )
    session.flush()
    from bot.services.reminders import sync_subtask_reminders

    sync_subtask_reminders(session, subtask, force=True)


def set_candidate_found(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
    candidate_name: str,
) -> TicketSubtask:
    """Этап: найден преподаватель (ФИО). Пишет в историю предложений."""
    name = candidate_name.strip()
    if len(name) < 2:
        raise ValueError("Укажите ФИО кандидата.")
    ticket = _require_open_replacement_subtask(session, subtask, actor)

    # предыдущие active → superseded
    for prop in list(subtask.proposals or []):
        if prop.outcome == ProposalOutcome.ACTIVE:
            prop.outcome = ProposalOutcome.SUPERSEDED
            prop.updated_at = utcnow()

    proposal = ReplacementProposal(
        subtask_id=subtask.id,
        teacher_name=name,
        outcome=ProposalOutcome.ACTIVE,
        actor_user_id=actor.id,
        comment="Кандидат найден",
    )
    session.add(proposal)
    subtask.current_candidate_name = name
    _set_stage(
        session,
        subtask=subtask,
        ticket=ticket,
        actor=actor,
        new_stage=ReplacementStage.FOUND,
        comment=f"Кандидат: {name}",
    )
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_replacement_ticket_status(session, ticket=ticket, actor=actor)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_candidate_found",
        details=f"number={ticket.number}; subtask={subtask.id}; candidate={name}",
    )
    return subtask


def mark_offered_to_parent(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
) -> TicketSubtask:
    ticket = _require_open_replacement_subtask(session, subtask, actor)
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    if stage not in (ReplacementStage.FOUND, ReplacementStage.OFFERED):
        raise ValueError("Сначала укажите найденного преподавателя.")
    if not (subtask.current_candidate_name or "").strip():
        raise ValueError("Нет текущего кандидата.")
    _set_stage(
        session,
        subtask=subtask,
        ticket=ticket,
        actor=actor,
        new_stage=ReplacementStage.OFFERED,
        comment=f"Написали родителю про {subtask.current_candidate_name}",
    )
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_replacement_ticket_status(session, ticket=ticket, actor=actor)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_offered_to_parent",
        details=f"number={ticket.number}; subtask={subtask.id}",
    )
    return subtask


def mark_replacement_done(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
) -> TicketSubtask:
    ticket = _require_open_replacement_subtask(session, subtask, actor)
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    if stage not in (ReplacementStage.FOUND, ReplacementStage.OFFERED):
        raise ValueError("Выполнить можно после нахождения кандидата.")
    for prop in list(subtask.proposals or []):
        if prop.outcome == ProposalOutcome.ACTIVE:
            prop.outcome = ProposalOutcome.ACCEPTED
            prop.updated_at = utcnow()
    subtask.needs_senior_help = False
    _set_stage(
        session,
        subtask=subtask,
        ticket=ticket,
        actor=actor,
        new_stage=ReplacementStage.AGREED,
        comment=f"Согласовано: {subtask.current_candidate_name or '—'}",
    )
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_replacement_ticket_status(session, ticket=ticket, actor=actor)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_agreed",
        details=f"number={ticket.number}; subtask={subtask.id}",
    )
    return subtask


def search_another_candidate(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
    comment: str | None = None,
) -> TicketSubtask:
    """Искать другого: текущий кандидат → история, этап снова «ищем»."""
    ticket = _require_open_replacement_subtask(session, subtask, actor)
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    if stage not in (
        ReplacementStage.FOUND,
        ReplacementStage.OFFERED,
    ):
        raise ValueError("Искать другого можно после предложения кандидата.")

    for prop in list(subtask.proposals or []):
        if prop.outcome == ProposalOutcome.ACTIVE:
            prop.outcome = ProposalOutcome.REJECTED_SEARCH_ANOTHER
            prop.comment = (comment or "").strip() or prop.comment
            prop.updated_at = utcnow()

    prev = subtask.current_candidate_name
    subtask.current_candidate_name = None
    note = f"Ищем другого (был: {prev or '—'})"
    if comment and comment.strip():
        note = f"{note}. {comment.strip()}"
    _set_stage(
        session,
        subtask=subtask,
        ticket=ticket,
        actor=actor,
        new_stage=ReplacementStage.SEARCHING,
        comment=note,
    )
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_replacement_ticket_status(session, ticket=ticket, actor=actor)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_search_another",
        details=f"number={ticket.number}; subtask={subtask.id}; was={prev}",
    )
    return subtask


def request_senior_help(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
    comment: str | None = None,
) -> tuple[TicketSubtask, list[User]]:
    """Пометить «нужна помощь старшего» и вернуть список старших для уведомления."""
    ticket = _require_open_replacement_subtask(session, subtask, actor)
    subtask.needs_senior_help = True
    subtask.updated_at = utcnow()
    note = "Запрошена помощь старшего преподавателя"
    if comment and comment.strip():
        note = f"{note}: {comment.strip()}"
        if subtask.comment:
            subtask.comment = f"{subtask.comment}\n— {note}"
        else:
            subtask.comment = note
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            subtask_id=subtask.id,
            actor_user_id=actor.id,
            old_status=subtask.status,
            new_status=subtask.status,
            comment=note,
        )
    )
    session.flush()

    seniors = (
        session.query(User)
        .filter(
            User.active.is_(True),
            User.role.in_([UserRole.SENIOR_TEACHER, UserRole.ADMIN]),
        )
        .order_by(User.name.asc())
        .all()
    )
    # не уведомлять самого инициатора повторно как «получателя», но в список ок
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_senior_help_requested",
        details=(
            f"number={ticket.number}; subtask={subtask.id}; "
            f"notify={len(seniors)}"
        ),
    )
    return subtask, seniors


def close_student_not_continuing(
    session: Session,
    *,
    subtask: TicketSubtask,
    actor: User,
    reason: str,
) -> TicketSubtask:
    text = reason.strip()
    if len(text) < 2:
        raise ValueError("Укажите причину закрытия.")
    ticket = _require_open_replacement_subtask(session, subtask, actor)
    for prop in list(subtask.proposals or []):
        if prop.outcome == ProposalOutcome.ACTIVE:
            prop.outcome = ProposalOutcome.SUPERSEDED
            prop.updated_at = utcnow()
    subtask.close_reason = text
    subtask.needs_senior_help = False
    subtask.current_candidate_name = None
    _set_stage(
        session,
        subtask=subtask,
        ticket=ticket,
        actor=actor,
        new_stage=ReplacementStage.CLOSED_NO_CONTINUE,
        comment=f"Ученик не продолжает: {text}",
    )
    session.refresh(ticket)
    _ = ticket.subtasks
    sync_replacement_ticket_status(session, ticket=ticket, actor=actor)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="replacement_closed_no_continue",
        details=f"number={ticket.number}; subtask={subtask.id}",
    )
    return subtask


def format_senior_help_message(subtask: TicketSubtask, actor: User) -> str:
    ticket = subtask.ticket
    number = ticket.number if ticket else "?"
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    return (
        f"🆘 <b>Нужна помощь старшего</b>\n"
        f"Заявка №{number} · {TICKET_TYPE_LABELS_RU.get(ticket.ticket_type, '') if ticket else ''}\n"
        f"Преподаватель (уходит): {ticket.teacher_name if ticket else '—'}\n"
        f"Ученик: {subtask.student_name}\n"
        f"Предмет: {subtask.subject or '—'}\n"
        f"Расписание: {subtask.lesson_datetime}\n"
        f"Этап: {REPLACEMENT_STAGE_LABELS_RU[stage]}\n"
        f"Кандидат: {subtask.current_candidate_name or '—'}\n"
        f"Запросил: {actor.name}"
    )


def format_replacement_ticket_card(ticket: Ticket, *, with_history: bool = False) -> str:
    from bot.services.tickets import status_label_with_emoji

    progress = compute_replacement_progress(ticket)
    type_label = TICKET_TYPE_LABELS_RU[ticket.ticket_type]
    status_label = status_label_with_emoji(ticket.status)
    start = (
        ticket.absence_start_date.strftime("%d.%m.%Y")
        if ticket.absence_start_date
        else "—"
    )
    ret = (
        ticket.return_date.strftime("%d.%m.%Y") if ticket.return_date else "—"
    )

    lines = [
        f"🧾 <b>Заявка №{ticket.number}</b> — {type_label}",
        f"Статус: <b>{status_label}</b>",
        progress.format_line(),
        f"Преподаватель: {ticket.teacher_name}",
    ]
    if ticket.ticket_type == TicketType.DISMISSAL:
        lines.append(f"Последний день: {start}")
    else:
        lines.append(f"Период: {start} → {ret}")
    if ticket.comment:
        lines.append(f"Комментарий: {ticket.comment}")

    lines.append("")
    lines.append("<b>Ученики:</b>")
    for i, sub in enumerate(ticket.subtasks or [], start=1):
        stage = sub.replacement_stage or ReplacementStage.SEARCHING
        help_mark = " 🆘" if sub.needs_senior_help else ""
        lines.append(
            f"{i}. {sub.student_name} — "
            f"{REPLACEMENT_STAGE_LABELS_RU[stage]}{help_mark}\n"
            f"   {sub.subject or '—'} · {sub.lesson_datetime}\n"
            f"   Кандидат: {sub.current_candidate_name or '—'}"
        )

    if with_history:
        creator_name = ticket.creator.name if ticket.creator else "—"
        assignee_name = ticket.assignee.name if ticket.assignee else "не назначен"
        lines.extend(
            [
                "",
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


def format_replacement_subtask_card(subtask: TicketSubtask) -> str:
    from bot.services.tickets import status_label_with_emoji

    ticket = subtask.ticket
    number = ticket.number if ticket else "?"
    stage = subtask.replacement_stage or ReplacementStage.SEARCHING
    lines = [
        f"👤 <b>Подбор замены</b> · заявка №{number}",
        f"Этап: <b>{REPLACEMENT_STAGE_LABELS_RU[stage]}</b>",
        f"Статус: {status_label_with_emoji(subtask.status)}",
        f"Ученик: {subtask.student_name}",
        f"Предмет: {subtask.subject or '—'}",
        f"Расписание: {subtask.lesson_datetime}",
        f"Кандидат: {subtask.current_candidate_name or '—'}",
    ]
    if subtask.needs_senior_help:
        lines.append("🆘 Нужна помощь старшего")
    if subtask.close_reason:
        lines.append(f"Причина закрытия: {subtask.close_reason}")
    if subtask.comment:
        lines.append(f"Комментарий: {subtask.comment}")

    proposals = list(subtask.proposals or [])
    if proposals:
        lines.append("")
        lines.append("<b>История предложений:</b>")
        for prop in proposals:
            when = prop.created_at.strftime("%d.%m %H:%M") if prop.created_at else ""
            outcome = PROPOSAL_OUTCOME_LABELS_RU.get(prop.outcome, prop.outcome.value)
            lines.append(f"• {when}: {prop.teacher_name} — {outcome}")

    if ticket:
        progress = compute_replacement_progress(ticket)
        lines.append("")
        lines.append(progress.format_line())
        lines.append(f"Общая заявка: {status_label_with_emoji(ticket.status)}")
    return "\n".join(lines)


def format_replacement_preview(data: dict[str, Any]) -> str:
    kind = TicketType(data["kind"])
    students: list[dict[str, Any]] = list(data.get("students") or [])
    lines = [
        f"📋 <b>Превью: {TICKET_TYPE_LABELS_RU[kind]}</b>",
        f"Преподаватель: {data['teacher_name']}",
    ]
    if kind == TicketType.DISMISSAL:
        lines.append(f"Последний день: {data['absence_start_date']}")
    else:
        lines.append(f"Начало отсутствия: {data['absence_start_date']}")
        lines.append(f"Дата возвращения: {data.get('return_date') or '—'}")
    if data.get("comment"):
        lines.append(f"Комментарий: {data['comment']}")
    lines.append(f"Учеников: {len(students)}")
    lines.append("")
    for i, stu in enumerate(students, start=1):
        lines.append(
            f"{i}. {stu['student_name']}\n"
            f"   {stu['subject']} · {stu.get('schedule') or stu.get('lesson_datetime')}"
            + (f"\n   ✎ {stu['comment']}" if stu.get("comment") else "")
        )
    return "\n".join(lines)


def is_subtask_closed(subtask: TicketSubtask) -> bool:
    stage = subtask.replacement_stage
    if stage is not None:
        return stage in CLOSED_REPLACEMENT_STAGES
    return subtask.status in CLOSED_SUBTASK_STATUSES
