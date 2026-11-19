"""Уведомление дежурному при создании и заметка о замене для куратора."""

from __future__ import annotations

from sqlalchemy.orm import Session

from bot.db.models import (
    MassSubtaskAction,
    TicketInitiator,
    TicketType,
    UserRole,
)
from bot.services import schedule as schedule_service
from bot.services.duty import get_duty_curator
from bot.services.mass import create_mass_ticket
from bot.services.notify import (
    can_comment_ticket,
    format_new_ticket_duty_notification,
    recipients_for_new_ticket_duty,
    recipients_for_staff_comment,
)
from bot.services.tickets import (
    add_ticket_comment,
    can_view_ticket,
    create_ticket,
    format_ticket_card,
)
from bot.services.users import create_employee


def test_senior_created_ticket_notifies_duty(
    session: Session, senior_user, admin_user
):
    ksenia = create_employee(
        session,
        telegram_id=2101,
        name="Ксения",
        role=UserRole.CURATOR,
        actor_telegram_id=admin_user.telegram_id,
    )
    create_employee(
        session,
        telegram_id=2102,
        name="Юлия",
        role=UserRole.CURATOR,
        actor_telegram_id=admin_user.telegram_id,
    )
    session.commit()
    assert schedule_service.ensure_default_cycle(session) is not None
    session.commit()

    ticket = create_ticket(
        session,
        creator=senior_user,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванова",
        student_name="Петров",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="not_written",
        comment="Замена: Сидоров",
    )
    session.commit()

    recipients = recipients_for_new_ticket_duty(
        session, ticket=ticket, creator=senior_user
    )
    assert len(recipients) == 1
    assert recipients[0].role == UserRole.CURATOR
    assert recipients[0].id == get_duty_curator(session).id
    assert recipients[0].id != senior_user.id
    _ = ksenia
    text = format_new_ticket_duty_notification(ticket, creator=senior_user)
    assert "Новая заявка" in text
    assert "Сидоров" in text
    assert "Заметка о замене" in text


def test_curator_creator_who_is_duty_notifies_other_curator(
    session: Session, admin_user
):
    ksenia = create_employee(
        session,
        telegram_id=2101,
        name="Ксения",
        role=UserRole.CURATOR,
        actor_telegram_id=admin_user.telegram_id,
    )
    yulia = create_employee(
        session,
        telegram_id=2102,
        name="Юлия",
        role=UserRole.CURATOR,
        actor_telegram_id=admin_user.telegram_id,
    )
    session.commit()
    assert schedule_service.ensure_default_cycle(session) is not None
    session.commit()

    duty = get_duty_curator(session)
    assert duty is not None
    other = yulia if duty.id == ksenia.id else ksenia

    ticket = create_ticket(
        session,
        creator=duty,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванова",
        student_name="Петров",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="not_written",
    )
    session.commit()

    recipients = recipients_for_new_ticket_duty(
        session, ticket=ticket, creator=duty
    )
    assert other.id in {r.id for r in recipients}
    assert duty.id not in {r.id for r in recipients}


def test_senior_can_view_and_comment_mass(
    session: Session, curator_user: User, senior_user: User
):
    ticket = create_mass_ticket(
        session,
        creator=curator_user,
        teacher_name="Козлова",
        reason="Больничный",
        absence_period="20–25.11",
        initiator=TicketInitiator.TEACHER,
        students=[
            {
                "student_name": "Ученик 1",
                "student_external_id": "",
                "lesson_datetime": "21.11 14:00",
                "desired_new_datetime": "",
                "required_action": MassSubtaskAction.TEMP_REPLACEMENT,
                "comment": None,
            }
        ],
    )
    session.commit()

    assert can_view_ticket(senior_user, ticket) is True
    assert can_comment_ticket(senior_user, ticket) is True

    ticket, recipients, text = add_ticket_comment(
        session,
        ticket=ticket,
        actor=senior_user,
        comment="Выбрали замену: Морозова",
    )
    session.commit()
    assert "Морозова" in (ticket.comment or "")
    assert any(r.id == curator_user.id for r in recipients) or recipients
    assert "Морозова" in text

    card = format_ticket_card(ticket, with_history=False)
    assert "Заметка о замене" in card
    assert "Морозова" in card


def test_staff_comment_recipients_include_assignee(
    session: Session, curator_user, senior_user, admin_user
):
    ticket = create_ticket(
        session,
        creator=curator_user,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванова",
        student_name="Петров",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="not_written",
    )
    session.commit()

    recipients = recipients_for_staff_comment(
        session, ticket=ticket, actor=senior_user
    )
    ids = {u.id for u in recipients}
    assert curator_user.id in ids  # ответственный / дежурный
    assert admin_user.id in ids
    assert senior_user.id not in ids
