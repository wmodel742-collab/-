"""Создание, статусы, поиск и форматирование заявок на перенос."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    TICKET_INITIATOR_LABELS_RU,
    TICKET_STATUS_LABELS_RU,
    TICKET_TYPE_LABELS_RU,
    WEEKDAY_LABELS_RU,
    Ticket,
    TicketHistory,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
    utcnow,
)
from bot.services.duty import resolve_assignee_for_new_ticket
from bot.services.users import write_audit

ACTIVE_STATUSES: tuple[TicketStatus, ...] = (
    TicketStatus.NOT_PROCESSED,
    TicketStatus.WAITING_REPLY,
)

# Начальное состояние при создании → статус
INITIAL_STATE_TO_STATUS: dict[str, TicketStatus] = {
    "not_written": TicketStatus.NOT_PROCESSED,
    "written": TicketStatus.WAITING_REPLY,
    "done": TicketStatus.DONE,
}

INITIAL_STATE_LABELS_RU: dict[str, str] = {
    "not_written": "Ещё не написали",
    "written": "Уже написали",
    "done": "Уже выполнено",
}

TICKET_STATUS_EMOJI: dict[TicketStatus, str] = {
    TicketStatus.NOT_PROCESSED: "⏳",
    TicketStatus.WAITING_REPLY: "💬",
    TicketStatus.DONE: "✅",
    TicketStatus.REJECTED: "❌",
}

TICKET_STATUS_GROUP_LABELS_RU: dict[TicketStatus, str] = {
    TicketStatus.NOT_PROCESSED: "⏳ Не обработано",
    TicketStatus.WAITING_REPLY: "💬 Ждём ответа",
    TicketStatus.DONE: "✅ Выполнено",
    TicketStatus.REJECTED: "❌ Отказ",
}


def status_label_with_emoji(status: TicketStatus) -> str:
    emoji = TICKET_STATUS_EMOJI.get(status, "·")
    return f"{emoji} {TICKET_STATUS_LABELS_RU[status]}"


def format_transfer_summary(ticket: Ticket) -> str:
    """Кратко: было → стало (для карточки и списков)."""
    if ticket.ticket_type == TicketType.ONE_TIME:
        was = ticket.original_datetime or "—"
        became = ticket.new_datetime or "—"
        return f"{was} → {became}"
    if ticket.ticket_type == TicketType.PERMANENT:
        cur = WEEKDAY_LABELS_RU.get(ticket.current_weekday or 0, "—")
        new = WEEKDAY_LABELS_RU.get(ticket.new_weekday or 0, "—")
        time_s = ticket.lesson_time or "—"
        return f"{cur} → {new}, {time_s}"
    if ticket.ticket_type == TicketType.MASS:
        return ticket.absence_period or ticket.reason or "массовый перенос"
    from bot.db.models import REPLACEMENT_TICKET_TYPES

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        start = (
            ticket.absence_start_date.strftime("%d.%m.%Y")
            if ticket.absence_start_date
            else "—"
        )
        if ticket.return_date:
            return f"{start} → {ticket.return_date.strftime('%d.%m.%Y')}"
        return f"с {start}"
    return "—"


def next_ticket_number(session: Session) -> int:
    current = session.query(func.max(Ticket.number)).scalar()
    return int(current or 0) + 1


def create_ticket(
    session: Session,
    *,
    creator: User,
    ticket_type: TicketType,
    teacher_name: str,
    student_name: str,
    student_external_id: str | None = None,
    initiator: TicketInitiator = TicketInitiator.TEACHER,
    initial_state: str = "not_written",
    comment: str | None = None,
    original_datetime: str | None = None,
    new_datetime: str | None = None,
    current_weekday: int | None = None,
    new_weekday: int | None = None,
    lesson_time: str | None = None,
    schedule_start_date: date | None = None,
    nearest_lesson_date: date | None = None,
    assignee: User | None = None,
) -> Ticket:
    """Создать заявку и запись в журнале истории."""
    if initial_state not in INITIAL_STATE_TO_STATUS:
        raise ValueError(
            f"Неизвестное начальное состояние: {initial_state}. "
            f"Допустимо: {', '.join(INITIAL_STATE_TO_STATUS)}"
        )
    status = INITIAL_STATE_TO_STATUS[initial_state]

    teacher = teacher_name.strip()
    student = student_name.strip()
    # ID ученика опционален (совместимость со старыми данными); в UX не спрашиваем
    student_id = (student_external_id or "").strip() or None
    if not teacher:
        raise ValueError("Укажите преподавателя.")
    if not student:
        raise ValueError("Укажите имя ученика.")

    if ticket_type == TicketType.ONE_TIME:
        if not (original_datetime or "").strip() or not (new_datetime or "").strip():
            raise ValueError("Для разового переноса нужны исходное и новое время.")
    elif ticket_type == TicketType.PERMANENT:
        if current_weekday is None or new_weekday is None:
            raise ValueError("Для постоянного переноса укажите текущий и новый день недели.")
        if not (1 <= current_weekday <= 7 and 1 <= new_weekday <= 7):
            raise ValueError("День недели должен быть от 1 (пн) до 7 (вс).")
        if not (lesson_time or "").strip():
            raise ValueError("Для постоянного переноса укажите время занятия.")
        if schedule_start_date is None or nearest_lesson_date is None:
            raise ValueError(
                "Для постоянного переноса нужны дата начала нового расписания "
                "и дата ближайшего затронутого занятия."
            )

    if assignee is None:
        assignee = resolve_assignee_for_new_ticket(session, creator)

    ticket = Ticket(
        number=next_ticket_number(session),
        ticket_type=ticket_type,
        status=status,
        teacher_name=teacher,
        student_name=student,
        student_external_id=student_id,
        original_datetime=(original_datetime or "").strip() or None,
        new_datetime=(new_datetime or "").strip() or None,
        current_weekday=current_weekday,
        new_weekday=new_weekday,
        lesson_time=(lesson_time or "").strip() or None,
        schedule_start_date=schedule_start_date,
        nearest_lesson_date=nearest_lesson_date,
        initiator=initiator,
        comment=(comment or "").strip() or None,
        creator_id=creator.id,
        assignee_id=assignee.id if assignee else None,
    )
    session.add(ticket)
    session.flush()

    history = TicketHistory(
        ticket_id=ticket.id,
        actor_user_id=creator.id,
        old_status=None,
        new_status=status,
        comment=f"Создана заявка. Начальное состояние: {INITIAL_STATE_LABELS_RU[initial_state]}",
    )
    session.add(history)
    session.flush()

    write_audit(
        session,
        actor_telegram_id=creator.telegram_id,
        action="ticket_created",
        details=f"number={ticket.number}; type={ticket_type.value}; status={status.value}",
    )
    from bot.services.reminders import sync_ticket_reminders
    from bot.services.warnings import dispatch_stability_warnings

    sync_ticket_reminders(session, ticket)
    dispatch_stability_warnings(session, ticket)
    return ticket


def get_ticket_by_id(session: Session, ticket_id: int) -> Ticket | None:
    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.history),
            joinedload(Ticket.subtasks),
        )
        .filter(Ticket.id == ticket_id)
        .one_or_none()
    )


def get_ticket_by_number(session: Session, number: int) -> Ticket | None:
    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.history),
            joinedload(Ticket.subtasks),
        )
        .filter(Ticket.number == number)
        .one_or_none()
    )


def list_active_tickets(session: Session) -> list[Ticket]:
    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.subtasks),
        )
        .filter(Ticket.status.in_(ACTIVE_STATUSES))
        .order_by(Ticket.created_at.desc())
        .all()
    )


def list_waiting_reply_tickets(session: Session) -> list[Ticket]:
    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.subtasks),
        )
        .filter(Ticket.status == TicketStatus.WAITING_REPLY)
        .order_by(Ticket.updated_at.desc())
        .all()
    )


def list_tickets_by_creator(session: Session, creator: User) -> list[Ticket]:
    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.subtasks),
        )
        .filter(Ticket.creator_id == creator.id)
        .order_by(Ticket.created_at.desc())
        .all()
    )


def search_tickets(session: Session, query: str) -> list[Ticket]:
    """Поиск по номеру, ID/имени ученика (в т.ч. подзадачи mass), преподавателю."""
    from bot.db.models import TicketSubtask

    q = query.strip()
    if not q:
        return []

    filters = [
        Ticket.student_external_id.ilike(f"%{q}%"),
        Ticket.student_name.ilike(f"%{q}%"),
        Ticket.teacher_name.ilike(f"%{q}%"),
        Ticket.reason.ilike(f"%{q}%"),
    ]
    if q.isdigit():
        filters.append(Ticket.number == int(q))

    sub_ids = (
        session.query(TicketSubtask.ticket_id)
        .filter(
            or_(
                TicketSubtask.student_external_id.ilike(f"%{q}%"),
                TicketSubtask.student_name.ilike(f"%{q}%"),
            )
        )
        .distinct()
    )
    filters.append(Ticket.id.in_(sub_ids))

    return (
        session.query(Ticket)
        .options(
            joinedload(Ticket.creator),
            joinedload(Ticket.assignee),
            joinedload(Ticket.subtasks),
        )
        .filter(or_(*filters))
        .order_by(Ticket.created_at.desc())
        .all()
    )


def can_manage_ticket(user: User, ticket: Ticket) -> bool:
    """Кто может менять статус/комментарий: админ, ответственный куратор, создатель-куратор."""
    from bot.db.models import REPLACEMENT_TICKET_TYPES

    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.CURATOR:
        if ticket.assignee_id == user.id or ticket.creator_id == user.id:
            return True
        # Кураторы видят и обрабатывают активные заявки школы
        return True
    if user.role == UserRole.SENIOR_TEACHER:
        if ticket.creator_id == user.id:
            return True
        # Старшие помогают по подбору замены (в т.ч. по запросу «нужна помощь»)
        return ticket.ticket_type in REPLACEMENT_TICKET_TYPES
    return False


def can_view_ticket(user: User, ticket: Ticket) -> bool:
    from bot.db.models import REPLACEMENT_TICKET_TYPES

    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.CURATOR:
        return True
    if user.role == UserRole.SENIOR_TEACHER:
        if ticket.creator_id == user.id:
            return True
        return ticket.ticket_type in REPLACEMENT_TICKET_TYPES
    return False


def change_ticket_status(
    session: Session,
    *,
    ticket: Ticket,
    new_status: TicketStatus,
    actor: User,
    comment: str | None = None,
) -> Ticket:
    """Сменить статус заявки. Отказ (rejected) не считается выполненным."""
    if not can_manage_ticket(actor, ticket):
        raise PermissionError("Недостаточно прав для изменения заявки.")

    old_status = ticket.status
    if old_status == new_status and not (comment or "").strip():
        return ticket

    ticket.status = new_status
    ticket.updated_at = utcnow()
    if comment and comment.strip():
        # дописываем в основное поле комментария кратко
        note = comment.strip()
        if ticket.comment:
            ticket.comment = f"{ticket.comment}\n— {note}"
        else:
            ticket.comment = note

    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            actor_user_id=actor.id,
            old_status=old_status,
            new_status=new_status,
            comment=(comment or "").strip() or None,
        )
    )
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="ticket_status_changed",
        details=(
            f"number={ticket.number}; "
            f"{old_status.value}->{new_status.value}"
        ),
    )
    from bot.services.reminders import sync_ticket_reminders

    sync_ticket_reminders(session, ticket)
    return ticket


def list_comment_notify_recipients(
    session: Session, *, actor: User | None = None
) -> list[User]:
    """Активные админы и старшие преподаватели (кроме автора комментария)."""
    recipients = (
        session.query(User)
        .filter(
            User.active.is_(True),
            User.role.in_([UserRole.ADMIN, UserRole.SENIOR_TEACHER]),
        )
        .order_by(User.id.asc())
        .all()
    )
    if actor is None:
        return list(recipients)
    return [u for u in recipients if u.id != actor.id]


def format_comment_notification(
    *,
    ticket_number: int,
    actor_name: str,
    comment: str,
    student_name: str | None = None,
) -> str:
    student_line = f"\nУченик: {student_name}" if student_name else ""
    return (
        f"💬 <b>Новый комментарий</b> к заявке №{ticket_number}\n"
        f"От: {actor_name}{student_line}\n"
        f"{comment.strip()}"
    )


def add_ticket_comment(
    session: Session,
    *,
    ticket: Ticket,
    actor: User,
    comment: str,
) -> tuple[Ticket, list[User], str]:
    """Добавить комментарий. Возвращает заявку, получателей уведомления и текст."""
    text = comment.strip()
    if not text:
        raise ValueError("Комментарий пустой.")
    if not can_manage_ticket(actor, ticket):
        raise PermissionError("Недостаточно прав для комментария.")

    if ticket.comment:
        ticket.comment = f"{ticket.comment}\n— {text}"
    else:
        ticket.comment = text
    ticket.updated_at = utcnow()
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            actor_user_id=actor.id,
            old_status=ticket.status,
            new_status=ticket.status,
            comment=text,
        )
    )
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id,
        action="ticket_comment_added",
        details=f"number={ticket.number}",
    )
    notify_text = format_comment_notification(
        ticket_number=ticket.number,
        actor_name=actor.name,
        comment=text,
        student_name=ticket.student_name,
    )
    recipients = list_comment_notify_recipients(session, actor=actor)
    return ticket, recipients, notify_text


def parse_date_ru(raw: str) -> date:
    """Принимает ДД.ММ.ГГГГ или ГГГГ-ММ-ДД."""
    text = raw.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Дата в формате ДД.ММ.ГГГГ (например 18.11.2026).")


def parse_weekday(raw: str) -> int:
    """1–7 или название дня недели."""
    text = raw.strip().lower()
    if text.isdigit():
        value = int(text)
        if 1 <= value <= 7:
            return value
        raise ValueError("День недели: число от 1 (пн) до 7 (вс).")
    aliases = {
        "пн": 1,
        "понедельник": 1,
        "вт": 2,
        "вторник": 2,
        "ср": 3,
        "среда": 3,
        "чт": 4,
        "четверг": 4,
        "пт": 5,
        "пятница": 5,
        "сб": 6,
        "суббота": 6,
        "вс": 7,
        "воскресенье": 7,
    }
    if text in aliases:
        return aliases[text]
    raise ValueError("Укажите день: пн…вс или число 1–7.")


def format_ticket_card(ticket: Ticket, *, with_history: bool = False) -> str:
    if ticket.ticket_type == TicketType.MASS:
        from bot.services.mass import format_mass_ticket_card

        return format_mass_ticket_card(ticket, with_history=with_history)

    from bot.db.models import REPLACEMENT_TICKET_TYPES

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        from bot.services.replacement import format_replacement_ticket_card

        return format_replacement_ticket_card(ticket, with_history=with_history)

    type_label = TICKET_TYPE_LABELS_RU[ticket.ticket_type]
    status_label = status_label_with_emoji(ticket.status)

    lines = [
        f"🧾 <b>Заявка №{ticket.number}</b> — {type_label}",
        f"Статус: <b>{status_label}</b>",
        f"Преподаватель: {ticket.teacher_name}",
        f"Ученик: {ticket.student_name or '—'}",
        f"Перенос: {format_transfer_summary(ticket)}",
    ]

    if ticket.ticket_type == TicketType.PERMANENT and with_history:
        lines.append(
            f"Старт нового расписания: "
            f"{ticket.schedule_start_date.strftime('%d.%m.%Y') if ticket.schedule_start_date else '—'}"
        )
        lines.append(
            f"Ближайшее занятие: "
            f"{ticket.nearest_lesson_date.strftime('%d.%m.%Y') if ticket.nearest_lesson_date else '—'}"
        )

    if ticket.comment:
        lines.append(f"Комментарий: {ticket.comment}")

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
            for entry in ticket.history[-8:]:
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
                lines.append(f"• {when}: {old} → {new}{note}")

    return "\n".join(lines)


def format_ticket_brief(ticket: Ticket) -> str:
    status = status_label_with_emoji(ticket.status)
    ttype = TICKET_TYPE_LABELS_RU[ticket.ticket_type]
    if ticket.ticket_type == TicketType.MASS:
        from bot.services.mass import compute_mass_progress

        progress = compute_mass_progress(ticket)
        return (
            f"{status} · №{ticket.number} · {ttype}\n"
            f"{ticket.teacher_name} · учеников {progress.total} "
            f"(готово {progress.done}/{progress.total})"
        )
    from bot.db.models import REPLACEMENT_TICKET_TYPES

    if ticket.ticket_type in REPLACEMENT_TICKET_TYPES:
        from bot.services.replacement import compute_replacement_progress

        progress = compute_replacement_progress(ticket)
        return (
            f"{status} · №{ticket.number} · {ttype}\n"
            f"{ticket.teacher_name} · учеников {progress.total} "
            f"(закрыто {progress.closed}/{progress.total})"
        )
    return (
        f"{status} · №{ticket.number} · {ttype}\n"
        f"{ticket.teacher_name} → {ticket.student_name or '—'}\n"
        f"{format_transfer_summary(ticket)}"
    )


def group_tickets_by_status(tickets: list[Ticket]) -> list[tuple[TicketStatus, list[Ticket]]]:
    """Группы в порядке: не обработано → ждём → выполнено → отказ."""
    order = (
        TicketStatus.NOT_PROCESSED,
        TicketStatus.WAITING_REPLY,
        TicketStatus.DONE,
        TicketStatus.REJECTED,
    )
    buckets: dict[TicketStatus, list[Ticket]] = {s: [] for s in order}
    for ticket in tickets:
        buckets.setdefault(ticket.status, []).append(ticket)
    return [(status, buckets[status]) for status in order if buckets.get(status)]


def format_ticket_list_text(
    tickets: list[Ticket],
    *,
    title: str,
) -> str:
    """Список с наглядными разделами по статусам."""
    if not tickets:
        return title
    lines = [f"<b>{title}</b> ({len(tickets)}):", ""]
    for status, group in group_tickets_by_status(tickets):
        lines.append(f"<b>{TICKET_STATUS_GROUP_LABELS_RU[status]}</b> ({len(group)})")
        for ticket in group[:20]:
            lines.append(format_ticket_brief(ticket))
            lines.append("")
    return "\n".join(lines).rstrip()


def ticket_payload_from_fsm(data: dict[str, Any]) -> dict[str, Any]:
    """Собрать kwargs для create_ticket из FSM data."""
    ticket_type = TicketType(data["ticket_type"])
    payload: dict[str, Any] = {
        "ticket_type": ticket_type,
        "teacher_name": data["teacher_name"],
        "student_name": data["student_name"],
        "student_external_id": data.get("student_external_id") or None,
        "initiator": TicketInitiator(data["initiator"]),
        "initial_state": data.get("initial_state", "not_written"),
        "comment": data.get("comment") or None,
    }
    if ticket_type == TicketType.ONE_TIME:
        payload["original_datetime"] = data["original_datetime"]
        payload["new_datetime"] = data["new_datetime"]
    else:
        payload["current_weekday"] = int(data["current_weekday"])
        payload["new_weekday"] = int(data["new_weekday"])
        payload["lesson_time"] = data["lesson_time"]
        payload["schedule_start_date"] = (
            data["schedule_start_date"]
            if isinstance(data["schedule_start_date"], date)
            else parse_date_ru(str(data["schedule_start_date"]))
        )
        payload["nearest_lesson_date"] = (
            data["nearest_lesson_date"]
            if isinstance(data["nearest_lesson_date"], date)
            else parse_date_ru(str(data["nearest_lesson_date"]))
        )
    return payload


def format_preview_from_fsm(data: dict[str, Any]) -> str:
    ticket_type = TicketType(data["ticket_type"])
    type_label = TICKET_TYPE_LABELS_RU[ticket_type]
    initiator = TicketInitiator(data["initiator"])
    initial = data.get("initial_state", "not_written")
    lines = [
        f"📋 <b>Превью: {type_label}</b>",
        f"Преподаватель: {data['teacher_name']}",
        f"Ученик: {data['student_name']}",
    ]
    if ticket_type == TicketType.ONE_TIME:
        lines.append(
            f"Перенос: {data['original_datetime']} → {data['new_datetime']}"
        )
    else:
        cur = WEEKDAY_LABELS_RU.get(int(data["current_weekday"]), str(data["current_weekday"]))
        new = WEEKDAY_LABELS_RU.get(int(data["new_weekday"]), str(data["new_weekday"]))
        start = data["schedule_start_date"]
        nearest = data["nearest_lesson_date"]
        if isinstance(start, date):
            start = start.strftime("%d.%m.%Y")
        if isinstance(nearest, date):
            nearest = nearest.strftime("%d.%m.%Y")
        lines.append(f"Перенос: {cur} → {new}, {data['lesson_time']}")
        lines.append(f"Старт нового расписания: {start}")
        lines.append(f"Ближайшее занятие: {nearest}")
    lines.append(f"Инициатор: {TICKET_INITIATOR_LABELS_RU[initiator]}")
    lines.append(f"Начальное состояние: {INITIAL_STATE_LABELS_RU.get(initial, initial)}")
    if data.get("comment"):
        lines.append(f"Комментарий: {data['comment']}")
    return "\n".join(lines)
