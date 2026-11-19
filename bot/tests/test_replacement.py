"""Этап 4: увольнение / временное отсутствие, этапы, история, помощь старшему."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from bot.db.models import (
    ProposalOutcome,
    ReplacementStage,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
    UserRole,
)
from bot.services.replacement import (
    close_student_not_continuing,
    compute_replacement_progress,
    create_replacement_ticket,
    get_replacement_subtask,
    mark_offered_to_parent,
    mark_replacement_done,
    request_senior_help,
    search_another_candidate,
    set_candidate_found,
)
from bot.services.tickets import (
    can_manage_ticket,
    can_view_ticket,
    get_ticket_by_number,
    list_active_tickets,
    search_tickets,
)
from bot.services.users import create_employee


def _students(n: int = 2) -> list[dict]:
    result = []
    for i in range(1, n + 1):
        result.append(
            {
                "student_name": f"Ученик Замена {i}",
                "student_external_id": f"REPL-{100 + i}",
                "subject": "Математика" if i % 2 else "Русский",
                "schedule": f"пн/ср {14 + i}:00",
                "comment": f"зам {i}" if i % 2 else None,
            }
        )
    return result


def _create_dismissal(session: Session, creator: User, n: int = 2, **overrides):
    kwargs = dict(
        kind=TicketType.DISMISSAL,
        teacher_name="Иванова А.П.",
        absence_start_date="20.11.2026",
        students=_students(n),
        comment="увольнение",
    )
    kwargs.update(overrides)
    return create_replacement_ticket(session, creator=creator, **kwargs)


def _create_temp(session: Session, creator: User, n: int = 2, **overrides):
    kwargs = dict(
        kind=TicketType.TEMPORARY_ABSENCE,
        teacher_name="Петров С.И.",
        absence_start_date="21.11.2026",
        return_date="05.12.2026",
        students=_students(n),
        comment="больничный",
    )
    kwargs.update(overrides)
    return create_replacement_ticket(session, creator=creator, **kwargs)


def test_create_dismissal_with_subtasks(session: Session, curator_user: User):
    ticket = _create_dismissal(session, curator_user, n=2)
    session.commit()

    assert ticket.ticket_type == TicketType.DISMISSAL
    assert ticket.number == 1
    assert ticket.status == TicketStatus.NOT_PROCESSED
    assert ticket.initiator == TicketInitiator.SCHOOL
    assert ticket.absence_start_date is not None
    assert ticket.return_date is None
    assert ticket.student_name is None
    assert len(ticket.subtasks) == 2
    for sub in ticket.subtasks:
        assert sub.replacement_stage == ReplacementStage.SEARCHING
        assert sub.status == TicketStatus.NOT_PROCESSED
        assert sub.required_action is None
        assert sub.subject
        assert sub.needs_senior_help is False
    progress = compute_replacement_progress(ticket)
    assert progress.total == 2
    assert progress.searching == 2
    assert progress.all_closed is False


def test_create_temporary_absence_requires_return(
    session: Session, curator_user: User
):
    with pytest.raises(ValueError, match="возвращ"):
        create_replacement_ticket(
            session,
            creator=curator_user,
            kind=TicketType.TEMPORARY_ABSENCE,
            teacher_name="X",
            absence_start_date="21.11.2026",
            return_date=None,
            students=_students(1),
        )

    ticket = _create_temp(session, curator_user, n=1)
    session.commit()
    assert ticket.ticket_type == TicketType.TEMPORARY_ABSENCE
    assert ticket.return_date is not None
    assert ticket.absence_start_date is not None


def test_stages_history_and_search_another(
    session: Session, curator_user: User
):
    ticket = _create_dismissal(session, curator_user, n=1)
    session.commit()
    sub = ticket.subtasks[0]

    set_candidate_found(
        session, subtask=sub, actor=curator_user, candidate_name="Сидорова Н."
    )
    session.commit()
    sub = get_replacement_subtask(session, sub.id)
    assert sub is not None
    assert sub.replacement_stage == ReplacementStage.FOUND
    assert sub.current_candidate_name == "Сидорова Н."
    assert len(sub.proposals) == 1
    assert sub.proposals[0].outcome == ProposalOutcome.ACTIVE

    mark_offered_to_parent(session, subtask=sub, actor=curator_user)
    session.commit()
    sub = get_replacement_subtask(session, sub.id)
    assert sub is not None
    assert sub.replacement_stage == ReplacementStage.OFFERED
    assert sub.status == TicketStatus.WAITING_REPLY
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert ticket.status == TicketStatus.WAITING_REPLY

    search_another_candidate(session, subtask=sub, actor=curator_user, comment="не подошёл")
    session.commit()
    sub = get_replacement_subtask(session, sub.id)
    assert sub is not None
    assert sub.replacement_stage == ReplacementStage.SEARCHING
    assert sub.current_candidate_name is None
    assert len(sub.proposals) == 1
    assert sub.proposals[0].outcome == ProposalOutcome.REJECTED_SEARCH_ANOTHER

    set_candidate_found(
        session, subtask=sub, actor=curator_user, candidate_name="Козлов В."
    )
    session.commit()
    sub = get_replacement_subtask(session, sub.id)
    assert sub is not None
    assert len(sub.proposals) == 2
    outcomes = {p.teacher_name: p.outcome for p in sub.proposals}
    assert outcomes["Сидорова Н."] == ProposalOutcome.REJECTED_SEARCH_ANOTHER
    assert outcomes["Козлов В."] == ProposalOutcome.ACTIVE

    mark_offered_to_parent(session, subtask=sub, actor=curator_user)
    mark_replacement_done(session, subtask=sub, actor=curator_user)
    session.commit()
    sub = get_replacement_subtask(session, sub.id)
    assert sub is not None
    assert sub.replacement_stage == ReplacementStage.AGREED
    assert sub.status == TicketStatus.DONE
    active = [p for p in sub.proposals if p.teacher_name == "Козлов В."]
    assert active[0].outcome == ProposalOutcome.ACCEPTED

    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert ticket.status == TicketStatus.DONE
    progress = compute_replacement_progress(ticket)
    assert progress.all_closed is True


def test_parent_not_closed_until_all_subtasks(
    session: Session, curator_user: User
):
    ticket = _create_dismissal(session, curator_user, n=2)
    session.commit()
    s1, s2 = ticket.subtasks

    set_candidate_found(
        session, subtask=s1, actor=curator_user, candidate_name="Алексеева А."
    )
    mark_offered_to_parent(session, subtask=s1, actor=curator_user)
    mark_replacement_done(session, subtask=s1, actor=curator_user)
    session.commit()

    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert ticket.status != TicketStatus.DONE
    progress = compute_replacement_progress(ticket)
    assert progress.agreed == 1
    assert progress.searching == 1

    close_student_not_continuing(
        session, subtask=s2, actor=curator_user, reason="переезд"
    )
    session.commit()
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert ticket.status == TicketStatus.DONE
    s2 = get_replacement_subtask(session, s2.id)
    assert s2 is not None
    assert s2.replacement_stage == ReplacementStage.CLOSED_NO_CONTINUE
    assert s2.close_reason == "переезд"
    assert s2.status == TicketStatus.REJECTED


def test_senior_help_flags_and_lists_seniors(
    session: Session, curator_user: User, senior_user: User, admin_user: User
):
    ticket = _create_temp(session, curator_user, n=1)
    session.commit()
    sub = ticket.subtasks[0]

    sub, seniors = request_senior_help(
        session, subtask=sub, actor=curator_user, comment="срочно"
    )
    session.commit()

    assert sub.needs_senior_help is True
    ids = {u.id for u in seniors}
    assert senior_user.id in ids
    assert admin_user.id in ids
    # куратор не в списке старших
    assert curator_user.id not in ids

    progress = compute_replacement_progress(get_ticket_by_number(session, ticket.number))
    assert progress.needs_help == 1

    # старший видит и может вести заявку замены
    ticket = get_ticket_by_number(session, ticket.number)
    assert ticket is not None
    assert can_view_ticket(senior_user, ticket) is True
    assert can_manage_ticket(senior_user, ticket) is True


def test_search_and_persistence(
    session: Session,
    session_factory,
    curator_user: User,
):
    ticket = _create_dismissal(session, curator_user, n=1)
    session.commit()
    number = ticket.number
    sid = ticket.subtasks[0].student_external_id

    found = search_tickets(session, "Иванова")
    assert any(t.number == number for t in found)
    found2 = search_tickets(session, sid)
    assert any(t.number == number for t in found2)
    assert any(t.number == number for t in list_active_tickets(session))

    # новая сессия — данные на месте
    with session_factory() as s2:
        t2 = get_ticket_by_number(s2, number)
        assert t2 is not None
        assert t2.ticket_type == TicketType.DISMISSAL
        assert len(t2.subtasks) == 1
        assert t2.subtasks[0].subject == "Математика"


def test_senior_can_create_replacement(session: Session, senior_user: User):
    ticket = _create_dismissal(session, senior_user, n=1)
    session.commit()
    assert ticket.creator_id == senior_user.id
    assert ticket.ticket_type == TicketType.DISMISSAL
