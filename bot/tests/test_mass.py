"""Этап 3: массовые заявки, независимые подзадачи, прогресс, персистентность."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from bot.db.models import (
    MassSubtaskAction,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.services.mass import (
    add_subtask_comment,
    change_subtask_status,
    compute_mass_progress,
    create_mass_ticket,
    derive_mass_parent_status,
    get_subtask_by_id,
)
from bot.services.tickets import (
    get_ticket_by_number,
    list_active_tickets,
    search_tickets,
)
from bot.services.users import create_employee


def _students(n: int = 3) -> list[dict]:
    result = []
    actions = list(MassSubtaskAction)
    for i in range(1, n + 1):
        result.append(
            {
                "student_name": f"Ученик {i}",
                "student_external_id": f"MASS-{100 + i}",
                "lesson_datetime": f"2{i}.11.2026 14:00",
                "desired_new_datetime": f"2{i}.11.2026 16:00",
                "required_action": actions[(i - 1) % len(actions)],
                "comment": f"коммент {i}" if i % 2 else None,
            }
        )
    return result


def _create_mass(session: Session, creator: User, n: int = 3, **overrides):
    kwargs = dict(
        teacher_name="Козлова М.",
        reason="Больничный",
        absence_period="20.11.2026 – 27.11.2026",
        initiator=TicketInitiator.TEACHER,
        students=_students(n),
        comment="общая заметка",
    )
    kwargs.update(overrides)
    return create_mass_ticket(session, creator=creator, **kwargs)


def test_create_mass_ticket_with_subtasks(session: Session, curator_user: User):
    ticket = _create_mass(session, curator_user, n=3)
    session.commit()

    assert ticket.ticket_type == TicketType.MASS
    assert ticket.number == 1
    assert ticket.status == TicketStatus.NOT_PROCESSED
    assert ticket.student_name is None
    assert ticket.reason == "Больничный"
    assert len(ticket.subtasks) == 3
    assert all(s.status == TicketStatus.NOT_PROCESSED for s in ticket.subtasks)
    progress = compute_mass_progress(ticket)
    assert progress.total == 3
    assert progress.done == 0
    assert progress.waiting == 0
    assert progress.not_processed == 3
    assert progress.all_closed is False


def test_independent_subtask_statuses_and_progress(
    session: Session, curator_user: User
):
    ticket = _create_mass(session, curator_user, n=3)
    session.commit()
    s1, s2, s3 = ticket.subtasks

    change_subtask_status(
        session,
        subtask=s1,
        new_status=TicketStatus.WAITING_REPLY,
        actor=curator_user,
        comment="Написали",
    )
    session.commit()
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    progress = compute_mass_progress(ticket)
    assert progress.waiting == 1
    assert progress.not_processed == 2
    assert ticket.status == TicketStatus.WAITING_REPLY
    assert progress.all_closed is False

    change_subtask_status(
        session,
        subtask=s2,
        new_status=TicketStatus.DONE,
        actor=curator_user,
    )
    session.commit()
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    # одна подзадача ещё не_обработано + одна ждёт — общая НЕ закрыта
    assert ticket.status != TicketStatus.DONE
    progress = compute_mass_progress(ticket)
    assert progress.done == 1
    assert progress.waiting == 1
    assert progress.not_processed == 1

    # отказ по третьей — тоже «закрытие» подзадачи
    change_subtask_status(
        session,
        subtask=s3,
        new_status=TicketStatus.REJECTED,
        actor=curator_user,
        comment="Отказ",
    )
    # закрыть оставшуюся waiting
    change_subtask_status(
        session,
        subtask=s1,
        new_status=TicketStatus.DONE,
        actor=curator_user,
    )
    session.commit()
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    progress = compute_mass_progress(ticket)
    assert progress.all_closed is True
    assert ticket.status == TicketStatus.DONE
    assert derive_mass_parent_status(progress) == TicketStatus.DONE


def test_parent_not_closed_before_all_subtasks(
    session: Session, curator_user: User
):
    ticket = _create_mass(session, curator_user, n=2)
    session.commit()
    s1, s2 = ticket.subtasks

    change_subtask_status(
        session, subtask=s1, new_status=TicketStatus.DONE, actor=curator_user
    )
    session.commit()
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert ticket.status in (
        TicketStatus.NOT_PROCESSED,
        TicketStatus.WAITING_REPLY,
    )
    assert ticket.status != TicketStatus.DONE
    assert s2.status == TicketStatus.NOT_PROCESSED

    active = list_active_tickets(session)
    assert any(t.id == ticket.id for t in active)


def test_search_finds_mass_by_student_and_teacher(
    session: Session, curator_user: User
):
    ticket = _create_mass(session, curator_user, n=2)
    session.commit()

    by_teacher = search_tickets(session, "Козлова")
    assert any(t.id == ticket.id for t in by_teacher)

    by_student = search_tickets(session, "MASS-101")
    assert any(t.id == ticket.id for t in by_student)

    by_name = search_tickets(session, "Ученик 2")
    assert any(t.id == ticket.id for t in by_name)

    by_number = search_tickets(session, str(ticket.number))
    assert len(by_number) == 1
    assert by_number[0].ticket_type == TicketType.MASS


def test_subtask_comment(session: Session, curator_user: User):
    ticket = _create_mass(session, curator_user, n=1)
    session.commit()
    sub = ticket.subtasks[0]
    sub, _recipients, notify_text = add_subtask_comment(
        session, subtask=sub, actor=curator_user, comment="Позвонили родителю"
    )
    session.commit()
    loaded = get_subtask_by_id(session, sub.id)
    assert loaded is not None
    assert "Позвонили родителю" in (loaded.comment or "")
    assert "Позвонили родителю" in notify_text
    assert str(ticket.number) in notify_text


def test_mass_requires_students(session: Session, curator_user: User):
    with pytest.raises(ValueError, match="ученика"):
        create_mass_ticket(
            session,
            creator=curator_user,
            teacher_name="X",
            reason="Y",
            absence_period="1–2",
            initiator=TicketInitiator.SCHOOL,
            students=[],
        )


def test_mass_persistence_across_sessions(tmp_path):
    from bot.services.bootstrap import ensure_admin_from_env, init_db, seed_default_settings

    url = f"sqlite:///{(tmp_path / 'stage3_persist.db').as_posix()}"
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
        ticket = _create_mass(s1, curator, n=2)
        number = ticket.number
        sub_id = ticket.subtasks[0].id
        s1.commit()

    with factory() as s2:
        loaded = get_ticket_by_number(s2, number)
        assert loaded is not None
        assert loaded.ticket_type == TicketType.MASS
        assert len(loaded.subtasks) == 2
        assert loaded.status == TicketStatus.NOT_PROCESSED

        sub = get_subtask_by_id(s2, sub_id)
        assert sub is not None
        curator2 = sub.ticket.assignee or loaded.creator
        change_subtask_status(
            s2,
            subtask=sub,
            new_status=TicketStatus.WAITING_REPLY,
            actor=curator2,
        )
        s2.commit()

    with factory() as s3:
        loaded = get_ticket_by_number(s3, number)
        assert loaded is not None
        progress = compute_mass_progress(loaded)
        assert progress.waiting == 1
        assert progress.not_processed == 1
        assert loaded.status == TicketStatus.WAITING_REPLY
        assert loaded.status != TicketStatus.DONE
