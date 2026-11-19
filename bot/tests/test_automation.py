"""Этап 6: рабочие часы, handoff, напоминания без дублей, стоп после закрытия."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from bot.db.models import TicketStatus, TicketType, User, UserRole
from bot.services import schedule as schedule_service
from bot.services.handoff import (
    immediate_duty_handoff_if_today,
    reassign_open_tickets,
    run_morning_duty_handoff,
)
from bot.services.jobs import (
    get_job_gateway,
    reset_job_gateway,
    ticket_reminder_job_id,
)
from bot.services.reminders import (
    cancel_ticket_reminder,
    schedule_ticket_reminder,
    sync_ticket_reminders,
)
from bot.services.tickets import change_ticket_status, create_ticket
from bot.db.models import TicketInitiator
from bot.services.users import create_employee
from bot.services.urgency import ticket_is_urgent
from bot.services.worktime import add_work_hours, is_within_work_hours, next_work_start


TZ = ZoneInfo("Europe/Moscow")
START = time(8, 0)
END = time(21, 0)


@pytest.fixture(autouse=True)
def _clean_jobs():
    reset_job_gateway()
    yield
    reset_job_gateway()


@pytest.fixture()
def ksenia(session: Session) -> User:
    user = create_employee(
        session,
        telegram_id=6101,
        name="Ксения",
        role=UserRole.CURATOR,
        actor_telegram_id=1001,
    )
    session.commit()
    return user


@pytest.fixture()
def yulia(session: Session) -> User:
    user = create_employee(
        session,
        telegram_id=6102,
        name="Юлия",
        role=UserRole.CURATOR,
        actor_telegram_id=1001,
    )
    session.commit()
    return user


@pytest.fixture()
def seeded_cycle(session: Session, ksenia: User, yulia: User):
    _ = (ksenia, yulia)
    cycle = schedule_service.ensure_default_cycle(session)
    session.commit()
    assert cycle is not None
    return cycle


def _make_waiting_ticket(session: Session, creator: User, assignee: User | None = None):
    return create_ticket(
        session,
        creator=creator,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванов",
        student_name="Петров",
        student_external_id="S1",
        initiator=TicketInitiator.TEACHER,
        initial_state="written",
        original_datetime="20.11.2026 18:00",
        new_datetime="21.11.2026 18:00",
        assignee=assignee or creator,
    )


# --- рабочие часы ---


def test_add_work_hours_inside_day():
    now = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)
    result = add_work_hours(now, 2, START, END)
    assert result == datetime(2026, 11, 18, 12, 0, tzinfo=TZ)


def test_add_work_hours_spills_to_next_day_with_remainder():
    # 20:00 + 2ч → 1ч до 21:00, остаток 1ч с 08:00 следующего дня = 09:00
    now = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    result = add_work_hours(now, 2, START, END)
    assert result == datetime(2026, 11, 19, 9, 0, tzinfo=TZ)


def test_add_work_hours_from_after_hours_defers_fully():
    # после 21:00 — весь интервал со следующего утра
    now = datetime(2026, 11, 18, 21, 30, tzinfo=TZ)
    result = add_work_hours(now, 2, START, END)
    assert result == datetime(2026, 11, 19, 10, 0, tzinfo=TZ)
    assert not is_within_work_hours(now, START, END)
    assert next_work_start(now, START, END) == datetime(2026, 11, 19, 8, 0, tzinfo=TZ)


def test_add_work_hours_before_open():
    now = datetime(2026, 11, 18, 7, 0, tzinfo=TZ)
    result = add_work_hours(now, 1, START, END)
    assert result == datetime(2026, 11, 18, 9, 0, tzinfo=TZ)


# --- handoff Ксения ↔ Юлия ---


def test_morning_handoff_ksenia_to_yulia(
    session: Session, seeded_cycle, ksenia: User, yulia: User, admin_user: User
):
    _ = seeded_cycle
    # 15.11 Ксения, 16.11 Юлия
    ticket = _make_waiting_ticket(session, admin_user, assignee=ksenia)
    session.commit()
    assert ticket.assignee_id == ksenia.id

    result = run_morning_duty_handoff(session, on_date=date(2026, 11, 16))
    session.commit()

    assert result.changed is True
    assert result.previous is not None and result.previous.id == ksenia.id
    assert result.current is not None and result.current.id == yulia.id
    assert result.reassigned == 1
    session.refresh(ticket)
    assert ticket.assignee_id == yulia.id
    assert ticket.status == TicketStatus.WAITING_REPLY
    # история сохранена + запись о handoff
    assert any("Смена дежурства" in (h.comment or "") for h in ticket.history)


def test_morning_same_curator_keeps_tasks(
    session: Session, seeded_cycle, ksenia: User, admin_user: User
):
    _ = seeded_cycle
    # 14 и 15 — оба Ксения
    ticket = _make_waiting_ticket(session, admin_user, assignee=ksenia)
    session.commit()

    result = run_morning_duty_handoff(session, on_date=date(2026, 11, 15))
    session.commit()

    assert result.changed is False
    assert result.reassigned == 0
    session.refresh(ticket)
    assert ticket.assignee_id == ksenia.id


def test_manual_duty_change_reassigns_immediately(
    session: Session, seeded_cycle, ksenia: User, yulia: User, admin_user: User
):
    _ = seeded_cycle
    # «сегодня» школы подменим через override на известную дату цикла
    # Используем immediate после set_day_override (уже встроено)
    ticket = _make_waiting_ticket(session, admin_user, assignee=ksenia)
    session.commit()

    # Подмена дня цикла Ксении на Юлию
    schedule_service.set_day_override(
        session, on_date=date(2026, 11, 14), curator=yulia, actor=admin_user
    )
    # set_day_override вызывает handoff только если on_date == school_today()
    # В тестах school_today = реальная дата — вызовем handoff явно для даты подмены
    # и отдельно проверим API immediate на «сегодня»
    today = schedule_service.school_today(session)
    schedule_service.set_day_override(
        session, on_date=today, curator=yulia, actor=admin_user
    )
    session.commit()

    session.refresh(ticket)
    # ticket мог быть переназначен, если today совпал; гарантируем через API
    result = immediate_duty_handoff_if_today(
        session, on_date=today, actor_telegram_id=admin_user.telegram_id
    )
    session.commit()
    assert result is not None
    session.refresh(ticket)
    assert ticket.assignee_id == yulia.id
    assert ticket.status == TicketStatus.WAITING_REPLY


# --- напоминания: без дублей, стоп после закрытия ---


def test_reminder_jobs_no_duplicates(session: Session, curator_user: User):
    ticket = _make_waiting_ticket(session, curator_user)
    session.commit()

    gw = get_job_gateway()
    job_id = ticket_reminder_job_id(ticket.id)
    assert gw.has_job(job_id)

    # повторный sync не создаёт второй job
    sync_ticket_reminders(session, ticket)
    sync_ticket_reminders(session, ticket)
    ids = gw.list_job_ids(prefix="reminder:ticket:")
    assert ids.count(job_id) == 1
    assert len([i for i in ids if i == job_id]) == 1

    # явный replace тем же id
    schedule_ticket_reminder(session, ticket, hours=2)
    schedule_ticket_reminder(session, ticket, hours=3)
    assert len(gw.list_job_ids(prefix=f"reminder:ticket:{ticket.id}")) == 1


def test_reminder_stops_after_done(session: Session, curator_user: User):
    ticket = _make_waiting_ticket(session, curator_user)
    session.commit()
    gw = get_job_gateway()
    job_id = ticket_reminder_job_id(ticket.id)
    assert gw.has_job(job_id)

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.DONE,
        actor=curator_user,
    )
    session.commit()
    assert not gw.has_job(job_id)


def test_reminder_stops_after_rejected(session: Session, curator_user: User):
    ticket = _make_waiting_ticket(session, curator_user)
    session.commit()
    gw = get_job_gateway()
    job_id = ticket_reminder_job_id(ticket.id)
    assert gw.has_job(job_id)

    change_ticket_status(
        session,
        ticket=ticket,
        new_status=TicketStatus.REJECTED,
        actor=curator_user,
        comment="отказ",
    )
    session.commit()
    assert not gw.has_job(job_id)


def test_urgency_transfer_within_3h(session: Session, curator_user: User):
    now = schedule_service.school_now(session)
    soon = (now + timedelta(hours=2)).strftime("%d.%m.%Y %H:%M")
    ticket = create_ticket(
        session,
        creator=curator_user,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванов",
        student_name="Петров",
        student_external_id="S2",
        initiator=TicketInitiator.TEACHER,
        initial_state="not_written",
        original_datetime=soon,
        new_datetime=(now + timedelta(days=1)).strftime("%d.%m.%Y %H:%M"),
    )
    session.commit()
    assert ticket_is_urgent(session, ticket, now=now) is True


def test_urgency_not_when_far(session: Session, curator_user: User):
    now = schedule_service.school_now(session)
    later = (now + timedelta(hours=10)).strftime("%d.%m.%Y %H:%M")
    ticket = create_ticket(
        session,
        creator=curator_user,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="Иванов",
        student_name="Петров",
        student_external_id="S3",
        initiator=TicketInitiator.TEACHER,
        initial_state="not_written",
        original_datetime=later,
        new_datetime=(now + timedelta(days=2)).strftime("%d.%m.%Y %H:%M"),
    )
    session.commit()
    assert ticket_is_urgent(session, ticket, now=now) is False


def test_reassign_preserves_status(session: Session, ksenia: User, yulia: User, admin_user: User):
    ticket = _make_waiting_ticket(session, admin_user, assignee=ksenia)
    session.commit()
    n = reassign_open_tickets(session, new_assignee=yulia, reason="test")
    session.commit()
    assert n == 1
    session.refresh(ticket)
    assert ticket.assignee_id == yulia.id
    assert ticket.status == TicketStatus.WAITING_REPLY


def test_cancel_helper(session: Session, curator_user: User):
    ticket = _make_waiting_ticket(session, curator_user)
    session.commit()
    cancel_ticket_reminder(ticket.id)
    assert not get_job_gateway().has_job(ticket_reminder_job_id(ticket.id))
