"""Этап 2: разовый/постоянный перенос, статусы, поиск, права, персистентность."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from bot.db.models import (
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.services.duty import get_duty_curator, resolve_assignee_for_new_ticket
from bot.services.tickets import (
    add_ticket_comment,
    can_manage_ticket,
    can_view_ticket,
    change_ticket_status,
    create_ticket,
    get_ticket_by_number,
    list_active_tickets,
    list_tickets_by_creator,
    list_waiting_reply_tickets,
    search_tickets,
)
from bot.services.users import create_employee


def _one_time_kwargs(**overrides):
    base = dict(
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванова А.",
        student_name="Петров Коля",
        student_external_id="STU-101",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="not_written",
        comment="просьба семьи",
    )
    base.update(overrides)
    return base


def _permanent_kwargs(**overrides):
    base = dict(
        ticket_type=TicketType.PERMANENT,
        teacher_name="Сидоров Б.",
        student_name="Смирнова Аня",
        student_external_id="STU-202",
        initiator=TicketInitiator.STUDENT_PARENT,
        current_weekday=2,
        new_weekday=4,
        lesson_time="15:30",
        schedule_start_date=date(2026, 12, 1),
        nearest_lesson_date=date(2026, 11, 25),
        initial_state="not_written",
    )
    base.update(overrides)
    return base


def test_create_one_time_ticket(session: Session, curator_user: User):
    ticket = create_ticket(session, creator=curator_user, **_one_time_kwargs())
    session.commit()

    assert ticket.number == 1
    assert ticket.ticket_type == TicketType.ONE_TIME
    assert ticket.status == TicketStatus.NOT_PROCESSED
    assert ticket.assignee_id == curator_user.id
    assert len(ticket.history) == 1
    assert ticket.history[0].new_status == TicketStatus.NOT_PROCESSED


def test_create_permanent_ticket(session: Session, senior_user: User, curator_user: User):
    ticket = create_ticket(session, creator=senior_user, **_permanent_kwargs())
    session.commit()

    assert ticket.ticket_type == TicketType.PERMANENT
    assert ticket.current_weekday == 2
    assert ticket.new_weekday == 4
    assert ticket.lesson_time == "15:30"
    assert ticket.schedule_start_date == date(2026, 12, 1)
    # старший → дежурный куратор (первый активный)
    assert ticket.assignee_id == curator_user.id


def test_initial_states_map_to_status(session: Session, curator_user: User):
    t1 = create_ticket(
        session, creator=curator_user, **_one_time_kwargs(initial_state="not_written")
    )
    t2 = create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(
            student_external_id="STU-102",
            initial_state="written",
        ),
    )
    t3 = create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(
            student_external_id="STU-103",
            initial_state="done",
        ),
    )
    session.commit()
    assert t1.status == TicketStatus.NOT_PROCESSED
    assert t2.status == TicketStatus.WAITING_REPLY
    assert t3.status == TicketStatus.DONE


def test_status_changes_and_rejection_is_not_done(
    session: Session, curator_user: User
):
    ticket = create_ticket(session, creator=curator_user, **_one_time_kwargs())
    session.commit()

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.WAITING_REPLY,
        actor=curator_user,
        comment="Написали ученику",
    )
    session.commit()
    assert ticket.status == TicketStatus.WAITING_REPLY

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.REJECTED,
        actor=curator_user,
        comment="Отказ",
    )
    session.commit()
    assert ticket.status == TicketStatus.REJECTED
    assert ticket.status != TicketStatus.DONE
    # отказ не попадает в активные и не считается выполненным
    active = list_active_tickets(session)
    assert all(t.id != ticket.id for t in active)
    waiting = list_waiting_reply_tickets(session)
    assert all(t.id != ticket.id for t in waiting)


def test_no_auto_done_on_waiting_actions(session: Session, curator_user: User):
    ticket = create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(initial_state="written"),
    )
    session.commit()
    assert ticket.status == TicketStatus.WAITING_REPLY

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.WAITING_REPLY,
        actor=curator_user,
        comment="Ответа пока нет",
    )
    session.commit()
    assert ticket.status == TicketStatus.WAITING_REPLY

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.WAITING_REPLY,
        actor=curator_user,
        comment="Предложили другое время",
    )
    session.commit()
    assert ticket.status == TicketStatus.WAITING_REPLY


def test_search_by_student_id_name_teacher_number(
    session: Session, curator_user: User
):
    t = create_ticket(session, creator=curator_user, **_one_time_kwargs())
    create_ticket(
        session,
        creator=curator_user,
        **_permanent_kwargs(),
    )
    session.commit()

    by_id = search_tickets(session, "STU-101")
    assert len(by_id) == 1
    assert by_id[0].id == t.id

    by_name = search_tickets(session, "Петров")
    assert any(x.id == t.id for x in by_name)

    by_teacher = search_tickets(session, "Иванова")
    assert any(x.id == t.id for x in by_teacher)

    by_number = search_tickets(session, str(t.number))
    assert len(by_number) == 1
    assert by_number[0].id == t.id


def test_permissions_senior_vs_curator(
    session: Session, curator_user: User, senior_user: User, admin_user: User
):
    mine = create_ticket(session, creator=senior_user, **_one_time_kwargs())
    other = create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(student_external_id="STU-999"),
    )
    session.commit()

    assert can_view_ticket(senior_user, mine) is True
    # Старший видит чужие переносы, чтобы оставить заметку о выбранной замене
    assert can_view_ticket(senior_user, other) is True
    assert can_manage_ticket(senior_user, mine) is True
    assert can_manage_ticket(senior_user, other) is False

    assert can_view_ticket(curator_user, mine) is True
    assert can_manage_ticket(curator_user, mine) is True
    assert can_view_ticket(admin_user, other) is True
    assert can_manage_ticket(admin_user, other) is True

    with pytest.raises(PermissionError):
        change_ticket_status(
            session,
            ticket=other,
            new_status=TicketStatus.DONE,
            actor=senior_user,
        )


def test_my_tickets_and_active_lists(
    session: Session, curator_user: User, senior_user: User
):
    create_ticket(session, creator=senior_user, **_one_time_kwargs())
    create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(
            student_external_id="STU-777",
            initial_state="written",
        ),
    )
    create_ticket(
        session,
        creator=curator_user,
        **_one_time_kwargs(
            student_external_id="STU-778",
            initial_state="done",
        ),
    )
    session.commit()

    mine = list_tickets_by_creator(session, senior_user)
    assert len(mine) == 1
    assert mine[0].creator_id == senior_user.id

    active = list_active_tickets(session)
    assert len(active) == 2
    assert all(t.status in (TicketStatus.NOT_PROCESSED, TicketStatus.WAITING_REPLY) for t in active)

    waiting = list_waiting_reply_tickets(session)
    assert len(waiting) == 1
    assert waiting[0].status == TicketStatus.WAITING_REPLY


def test_comment_history(session: Session, curator_user: User):
    ticket = create_ticket(session, creator=curator_user, **_one_time_kwargs())
    ticket, recipients, notify_text = add_ticket_comment(
        session, ticket=ticket, actor=curator_user, comment="Уточнили время"
    )
    session.commit()
    assert "Уточнили время" in (ticket.comment or "")
    assert any(h.comment == "Уточнили время" for h in ticket.history)
    assert "№" in notify_text and "Уточнили время" in notify_text
    # админ из env + старшие (если есть) — без автора-куратора
    assert all(r.id != curator_user.id for r in recipients)


def test_admin_assigns_to_self(session: Session, admin_user: User, curator_user: User):
    _ = curator_user
    ticket = create_ticket(session, creator=admin_user, **_one_time_kwargs())
    session.commit()
    assert ticket.assignee_id == admin_user.id


def test_get_duty_curator_fallback_without_cycle(
    session: Session, curator_user: User, admin_user: User
):
    """Без пары Ксения/Юлия — fallback на первого активного куратора."""
    _ = admin_user
    create_employee(
        session,
        telegram_id=2003,
        name="Второй куратор",
        role=UserRole.CURATOR,
        actor_telegram_id=1001,
    )
    session.commit()
    duty = get_duty_curator(session, date(2026, 11, 18))
    assert duty is not None
    assert duty.role == UserRole.CURATOR
    assert duty.id == curator_user.id  # первый по id

    resolved = resolve_assignee_for_new_ticket(session, curator_user)
    assert resolved.id == curator_user.id


def test_persistence_across_sessions(tmp_path):
    """Новая сессия БД видит ранее созданную заявку (как после «перезапуска»)."""
    from bot.services.bootstrap import ensure_admin_from_env, init_db, seed_default_settings

    url = f"sqlite:///{(tmp_path / 'stage2_persist.db').as_posix()}"
    factory = init_db(url)

    with factory() as s1:
        seed_default_settings(s1, "Europe/Moscow")
        ensure_admin_from_env(s1, 1001, admin_name="Rais Admin")
        curator = create_employee(
            s1,
            telegram_id=2002,
            name="Куратор Тест",
            role=UserRole.CURATOR,
            actor_telegram_id=1001,
        )
        ticket = create_ticket(s1, creator=curator, **_one_time_kwargs())
        number = ticket.number
        s1.commit()

    with factory() as s2:
        loaded = get_ticket_by_number(s2, number)
        assert loaded is not None
        assert loaded.student_external_id == "STU-101"
        assert loaded.ticket_type == TicketType.ONE_TIME
        assert loaded.status == TicketStatus.NOT_PROCESSED
        assert len(loaded.history) >= 1


def test_rejected_not_in_done_search_semantics(session: Session, curator_user: User):
    ticket = create_ticket(session, creator=curator_user, **_one_time_kwargs())
    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.REJECTED,
        actor=curator_user,
        comment="Отказ",
    )
    session.commit()
    assert ticket.status is TicketStatus.REJECTED
    assert ticket.status is not TicketStatus.DONE
