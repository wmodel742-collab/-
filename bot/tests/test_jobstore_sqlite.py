"""Jobstore отдельно от основной SQLite — без database is locked при создании."""

from __future__ import annotations

from bot.db.session import jobstore_url_from_database_url


def test_jobstore_url_separate_file():
    assert jobstore_url_from_database_url("sqlite:///./avoperenos.db").endswith(
        "avoperenos.jobs.db"
    )
    assert "avoperenos.db" not in jobstore_url_from_database_url(
        "sqlite:///./avoperenos.db"
    ).rsplit("/", 1)[-1].replace("avoperenos.jobs.db", "")
    jobs = jobstore_url_from_database_url("sqlite:////data/bot.db")
    assert jobs.endswith("bot.jobs.db")
    assert jobs.startswith("sqlite:///")


def test_jobstore_memory_and_non_sqlite():
    assert jobstore_url_from_database_url("sqlite:///:memory:") == "sqlite:///:memory:"
    assert (
        jobstore_url_from_database_url("postgresql://u:p@h/db")
        == "postgresql://u:p@h/db"
    )


def test_reminder_sync_does_not_raise_on_jobstore_error(session, curator_user):
    from bot.db.models import TicketInitiator, TicketType
    from bot.services.jobs import get_job_gateway
    from bot.services.reminders import cancel_ticket_reminder, sync_ticket_reminders
    from bot.services.tickets import create_ticket

    ticket = create_ticket(
        session,
        creator=curator_user,
        ticket_type=TicketType.ONE_TIME,
        teacher_name="T",
        student_name="S",
        initiator=TicketInitiator.TEACHER,
        original_datetime="20.11.2026 14:00",
        new_datetime="21.11.2026 16:00",
        initial_state="written",
    )
    session.flush()
    cancel_ticket_reminder(ticket.id)

    gw = get_job_gateway()
    original = gw.schedule_date

    def boom(*_a, **_k):
        raise Exception("database is locked")

    gw.schedule_date = boom  # type: ignore[method-assign]
    try:
        # не должно пробрасывать — иначе откат создания заявки
        sync_ticket_reminders(session, ticket)
    finally:
        gw.schedule_date = original  # type: ignore[method-assign]
