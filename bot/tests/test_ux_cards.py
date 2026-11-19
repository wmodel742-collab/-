"""UX: карточки без ID ученика, комментарии → уведомления, группировка статусов."""

from __future__ import annotations

from sqlalchemy.orm import Session

from bot.db.models import (
    Ticket,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.services.tickets import (
    add_ticket_comment,
    create_ticket,
    format_ticket_card,
    format_ticket_list_text,
    group_tickets_by_status,
    list_comment_notify_recipients,
)
from bot.services.users import create_employee


def _one_time(session: Session, creator: User, **overrides):
    base = dict(
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванова А.",
        student_name="Петров Коля",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="not_written",
        comment="просьба семьи",
    )
    base.update(overrides)
    return create_ticket(session, creator=creator, **base)


def test_create_without_student_id(session: Session, curator_user: User):
    ticket = _one_time(session, curator_user)
    session.commit()
    assert ticket.student_external_id is None
    assert ticket.student_name == "Петров Коля"


def test_card_hides_student_id_and_service_fields(
    session: Session, curator_user: User
):
    ticket = _one_time(
        session,
        curator_user,
        student_external_id="STU-HIDDEN",
        comment="видно куратору и админу",
    )
    session.commit()

    compact = format_ticket_card(ticket, with_history=False)
    assert "STU-HIDDEN" not in compact
    assert "ID " not in compact
    assert "Петров Коля" in compact
    assert "Иванова А." in compact
    assert "20.11.2026 14:00 → 21.11.2026 16:00" in compact
    assert "видно куратору и админу" in compact
    assert "Создал:" not in compact
    assert "Куратор:" not in compact
    assert "История:" not in compact
    assert "⏳" in compact or "Ещё не написали" in compact

    detailed = format_ticket_card(ticket, with_history=True)
    assert "Создал:" in detailed
    assert "Куратор:" in detailed
    assert "История:" in detailed
    assert "STU-HIDDEN" not in detailed


def test_comment_notifies_admins_and_seniors(
    session: Session, curator_user: User, admin_user: User, senior_user: User
):
    other_admin = create_employee(
        session,
        telegram_id=1002,
        name="Второй Админ",
        role=UserRole.ADMIN,
        actor_telegram_id=admin_user.telegram_id,
    )
    session.commit()

    ticket = _one_time(session, curator_user)
    session.commit()

    ticket, recipients, text = add_ticket_comment(
        session,
        ticket=ticket,
        actor=curator_user,
        comment="Нужна помощь с временем",
    )
    session.commit()

    ids = {u.id for u in recipients}
    assert admin_user.id in ids
    assert senior_user.id in ids
    assert other_admin.id in ids
    assert curator_user.id not in ids
    assert "№" in text
    assert str(ticket.number) in text
    assert "Нужна помощь с временем" in text
    assert "Новый комментарий" in text

    # автор-админ не получает своё же уведомление
    _, recipients_admin, _ = add_ticket_comment(
        session, ticket=ticket, actor=admin_user, comment="Ок"
    )
    assert all(r.id != admin_user.id for r in recipients_admin)


def test_list_groups_by_status(session: Session, curator_user: User):
    _one_time(session, curator_user, initial_state="not_written")
    _one_time(
        session,
        curator_user,
        student_name="Ждущий",
        initial_state="written",
    )
    done = _one_time(
        session,
        curator_user,
        student_name="Готовый",
        initial_state="done",
    )
    session.commit()

    _ = done
    tickets = session.query(Ticket).order_by(Ticket.id).all()
    groups = group_tickets_by_status(tickets)
    statuses = [s for s, _ in groups]
    assert statuses[0] == TicketStatus.NOT_PROCESSED
    assert TicketStatus.WAITING_REPLY in statuses
    assert TicketStatus.DONE in statuses

    text = format_ticket_list_text(tickets, title="Активные заявки")
    assert "⏳ Не обработано" in text
    assert "💬 Ждём ответа" in text
    assert "✅ Выполнено" in text
    assert "ID " not in text
    assert "Ждущий" in text


def test_list_comment_notify_recipients_only_active(
    session: Session, admin_user: User, senior_user: User, curator_user: User
):
    _ = curator_user
    create_employee(
        session,
        telegram_id=1003,
        name="Неактивный админ",
        role=UserRole.ADMIN,
        actor_telegram_id=admin_user.telegram_id,
    )
    inactive = (
        session.query(User)
        .filter(User.telegram_id == 1003)
        .one()
    )
    inactive.active = False
    session.commit()

    recipients = list_comment_notify_recipients(session)
    assert all(u.active for u in recipients)
    assert all(
        u.role in (UserRole.ADMIN, UserRole.SENIOR_TEACHER) for u in recipients
    )
    assert inactive.id not in {u.id for u in recipients}
    assert senior_user.id in {u.id for u in recipients}
