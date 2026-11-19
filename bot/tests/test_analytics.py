"""Этап 7: периоды, предупреждения стабильности, исключение parent/school."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from bot.db.models import (
    NotificationLog,
    TicketInitiator,
    TicketStatus,
    TicketType,
    User,
)
from bot.services.analytics import (
    PeriodKind,
    WARN_LEVEL_ADMIN,
    WARN_LEVEL_CURATOR,
    build_students_csv,
    build_transfers_csv,
    count_stability_transfers_for_student,
    format_weekly_summary,
    resolve_period,
    rolling_30d_range,
    student_stats_list,
    teacher_stats,
)
from bot.services.mass import create_mass_ticket
from bot.services.tickets import change_ticket_status, create_ticket
from bot.services.warnings import (
    KIND_STABILITY,
    evaluate_stability_warnings,
    format_warning_message,
)


TZ = ZoneInfo("Europe/Moscow")


def _make_one_time(
    session: Session,
    creator: User,
    *,
    student_id: str = "S1",
    student_name: str = "Ученик",
    teacher: str = "Иванов",
    initiator: TicketInitiator = TicketInitiator.TEACHER,
    original: str = "01.12.2026 10:00",
    new: str = "02.12.2026 11:00",
    created_at: datetime | None = None,
):
    ticket = create_ticket(
        session,
        creator=creator,
        ticket_type=TicketType.ONE_TIME,
        teacher_name=teacher,
        student_name=student_name,
        student_external_id=student_id,
        initiator=initiator,
        original_datetime=original,
        new_datetime=new,
    )
    if created_at is not None:
        ticket.created_at = created_at
        session.flush()
    session.commit()
    return ticket


def test_period_7_30_and_calendar_month(session: Session):
    ref = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
    p7 = resolve_period(session, PeriodKind.DAYS_7, ref=ref)
    assert p7.start_date == date(2026, 11, 12)
    assert p7.end_date_inclusive == date(2026, 11, 18)

    p30 = resolve_period(session, PeriodKind.DAYS_30, ref=ref)
    assert p30.start_date == date(2026, 10, 20)
    assert p30.end_date_inclusive == date(2026, 11, 18)

    month = resolve_period(session, PeriodKind.CALENDAR_MONTH, ref=ref)
    assert month.start_date == date(2026, 11, 1)
    assert month.end_date_inclusive == date(2026, 11, 30)
    assert "Ноябрь" in month.label


def test_period_custom(session: Session):
    rng = resolve_period(
        session,
        PeriodKind.CUSTOM,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 10),
    )
    assert rng.start_date == date(2026, 11, 1)
    assert rng.end_date_inclusive == date(2026, 11, 10)

    with pytest.raises(ValueError):
        resolve_period(
            session,
            PeriodKind.CUSTOM,
            start_date=date(2026, 11, 10),
            end_date=date(2026, 11, 1),
        )


def test_second_transfer_warning_for_curator(session: Session, curator_user: User):
    _make_one_time(
        session,
        curator_user,
        student_id="STU-WARN",
        original="10.11.2026 10:00",
        new="11.11.2026 10:00",
    )
    # 1-й — без предупреждения (dispatch при create)
    assert (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .count()
        == 0
    )

    t2 = _make_one_time(
        session,
        curator_user,
        student_id="STU-WARN",
        original="15.11.2026 10:00",
        new="16.11.2026 10:00",
    )
    # 2-й — предупреждение куратору уже при create_ticket
    logs = (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .all()
    )
    assert len(logs) == 1
    assert "level=curator" in (logs[0].details or "")
    assert count_stability_transfers_for_student(
        session, student_external_id="STU-WARN"
    ) == WARN_LEVEL_CURATOR

    # повторное открытие / повтор evaluate — без спама
    assert evaluate_stability_warnings(session, t2) == []
    assert (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .count()
        == 1
    )


def test_third_transfer_escalates_to_admin(session: Session, curator_user: User):
    for day in (5, 10, 15):
        _make_one_time(
            session,
            curator_user,
            student_id="STU-3",
            original=f"{day:02d}.11.2026 10:00",
            new=f"{day + 1:02d}.11.2026 11:00",
        )
    logs = (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .order_by(NotificationLog.id.asc())
        .all()
    )
    # 2-й и 3-й переносы → два события; последнее — эскалация админу
    assert len(logs) >= 2
    assert any("level=curator_admin" in (log.details or "") for log in logs)
    assert count_stability_transfers_for_student(
        session, student_external_id="STU-3"
    ) >= WARN_LEVEL_ADMIN


def test_parent_initiator_excluded_from_stability(
    session: Session, curator_user: User
):
    _make_one_time(
        session,
        curator_user,
        student_id="STU-P",
        initiator=TicketInitiator.STUDENT_PARENT,
        original="01.11.2026 10:00",
        new="02.11.2026 10:00",
    )
    _make_one_time(
        session,
        curator_user,
        student_id="STU-P",
        initiator=TicketInitiator.SCHOOL,
        original="03.11.2026 10:00",
        new="04.11.2026 10:00",
    )
    _make_one_time(
        session,
        curator_user,
        student_id="STU-P",
        initiator=TicketInitiator.TEACHER,
        original="05.11.2026 10:00",
        new="06.11.2026 10:00",
    )
    count = count_stability_transfers_for_student(
        session, student_external_id="STU-P"
    )
    assert count == 1
    assert (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .count()
        == 0
    )


def test_rejected_excluded_from_stability(session: Session, curator_user: User):
    t1 = _make_one_time(
        session,
        curator_user,
        student_id="STU-R",
        original="01.11.2026 10:00",
        new="02.11.2026 10:00",
    )
    change_ticket_status(
        session, ticket=t1, new_status=TicketStatus.REJECTED, actor=curator_user
    )
    session.commit()
    _make_one_time(
        session,
        curator_user,
        student_id="STU-R",
        original="08.11.2026 10:00",
        new="09.11.2026 10:00",
    )
    count = count_stability_transfers_for_student(
        session, student_external_id="STU-R"
    )
    assert count == 1
    assert (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .count()
        == 0
    )


def test_duplicate_fingerprint_not_double_counted(
    session: Session, curator_user: User
):
    _make_one_time(
        session,
        curator_user,
        student_id="STU-D",
        teacher="Петров",
        original="12.11.2026 10:00",
        new="13.11.2026 10:00",
    )
    # тот же fingerprint (ученик+преподаватель+исходное время)
    _make_one_time(
        session,
        curator_user,
        student_id="STU-D",
        teacher="Петров",
        original="12.11.2026 10:00",
        new="14.11.2026 12:00",
    )
    count = count_stability_transfers_for_student(
        session, student_external_id="STU-D"
    )
    assert count == 1


def test_mass_warns_per_student(session: Session, curator_user: User):
    # у каждого ученика уже по одному переносу
    for sid in ("M1", "M2"):
        _make_one_time(
            session,
            curator_user,
            student_id=sid,
            student_name=f"Уч {sid}",
            original="01.11.2026 10:00",
            new="02.11.2026 10:00",
        )
    mass = create_mass_ticket(
        session,
        creator=curator_user,
        teacher_name="Сидоров",
        reason="болезнь",
        absence_period="18–20.11",
        initiator=TicketInitiator.TEACHER,
        students=[
            {
                "student_name": "Уч M1",
                "student_external_id": "M1",
                "lesson_datetime": "18.11.2026 10:00",
                "required_action": "propose_transfer",
            },
            {
                "student_name": "Уч M2",
                "student_external_id": "M2",
                "lesson_datetime": "18.11.2026 11:00",
                "required_action": "propose_transfer",
            },
        ],
    )
    session.commit()
    logs = (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .all()
    )
    assert len(logs) == 2
    keys = {log.target_key for log in logs}
    assert any(":M1:" in k or "M1" in k for k in keys)
    assert any(":M2:" in k or "M2" in k for k in keys)
    # повтор evaluate — антиспам
    assert evaluate_stability_warnings(session, mass) == []
    assert count_stability_transfers_for_student(
        session, student_external_id="M1"
    ) == 2


def test_teacher_stats_cases_vs_lessons(session: Session, curator_user: User):
    create_mass_ticket(
        session,
        creator=curator_user,
        teacher_name="Козлова",
        reason="отпуск",
        absence_period="неделя",
        initiator=TicketInitiator.TEACHER,
        students=[
            {
                "student_name": "А",
                "student_external_id": "A1",
                "lesson_datetime": "20.11.2026 10:00",
                "required_action": "propose_transfer",
            },
            {
                "student_name": "Б",
                "student_external_id": "B1",
                "lesson_datetime": "21.11.2026 10:00",
                "required_action": "propose_transfer",
            },
        ],
    )
    session.commit()
    rng = rolling_30d_range(session)
    stats = teacher_stats(session, teacher_name="Козлова", date_range=rng)
    assert stats.transfer_cases == 1
    assert stats.affected_lessons == 2
    assert stats.unique_students == 2
    assert stats.absence_cases >= 1


def test_students_sorted_by_count(session: Session, curator_user: User):
    _make_one_time(session, curator_user, student_id="LOW", student_name="Мало")
    _make_one_time(
        session,
        curator_user,
        student_id="HIGH",
        student_name="Много",
        original="01.11.2026 09:00",
        new="02.11.2026 09:00",
    )
    _make_one_time(
        session,
        curator_user,
        student_id="HIGH",
        student_name="Много",
        original="05.11.2026 09:00",
        new="06.11.2026 09:00",
    )
    items = student_stats_list(session)
    assert items[0].student_external_id == "HIGH"
    assert items[0].transfer_count == 2
    assert items[1].student_external_id == "LOW"


def test_outside_30_day_window_not_counted(session: Session, curator_user: User):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    t_old = _make_one_time(
        session,
        curator_user,
        student_id="OLD",
        original="01.09.2026 10:00",
        new="02.09.2026 10:00",
        created_at=old,
    )
    assert t_old.created_at == old
    t_new = _make_one_time(
        session,
        curator_user,
        student_id="OLD",
        original="10.11.2026 10:00",
        new="11.11.2026 10:00",
    )
    count = count_stability_transfers_for_student(
        session, student_external_id="OLD"
    )
    assert count == 1
    assert (
        session.query(NotificationLog)
        .filter(NotificationLog.kind == KIND_STABILITY)
        .count()
        == 0
    )
    _ = t_new


def test_csv_and_weekly_summary(session: Session, curator_user: User):
    _make_one_time(session, curator_user, student_id="CSV1", student_name="Анна CSV")
    rng = rolling_30d_range(session)
    students_csv = build_students_csv(session, date_range=rng)
    transfers_csv = build_transfers_csv(session, date_range=rng)
    assert students_csv.startswith(b"\xef\xbb\xbf")
    assert b"student_external_id" in students_csv  # в выгрузке Excel — ок
    assert b"CSV1" in students_csv
    assert b"ticket_number" in transfers_csv
    text = format_weekly_summary(session)
    assert "Еженедельный отчёт" in text
    assert "Переносов" in text
    # в текстах бота ID ученика не акцентируем
    assert "ID CSV1" not in text
    assert "(ID " not in text
    warn_text = format_warning_message(
        student_name="Анна CSV",
        student_external_id="CSV1",
        count_30d=2,
        ticket_number=1,
        teacher_name="Иванов",
    )
    assert "Анна CSV" in warn_text
    assert "CSV1" not in warn_text
    assert "ID " not in warn_text
