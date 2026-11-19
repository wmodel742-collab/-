"""Этап 5: график кураторов 2/2, подмены, timezone из настроек."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from bot.db.models import ScheduleCycle, ScheduleDayOverride, SettingsRecord, User, UserRole
from bot.services import schedule as schedule_service
from bot.services.duty import get_duty_curator, resolve_assignee_for_new_ticket
from bot.services.users import create_employee


@pytest.fixture()
def ksenia(session: Session) -> User:
    user = create_employee(
        session,
        telegram_id=2101,
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
        telegram_id=2102,
        name="Юлия",
        role=UserRole.CURATOR,
        actor_telegram_id=1001,
    )
    session.commit()
    return user


@pytest.fixture()
def seeded_cycle(session: Session, ksenia: User, yulia: User) -> ScheduleCycle:
    _ = (ksenia, yulia)
    cycle = schedule_service.ensure_default_cycle(session)
    session.commit()
    assert cycle is not None
    return cycle


def test_anchor_days_match_spec(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    _ = seeded_cycle
    # 14–15.11.2026 Ксения, 16–17 Юлия, 18–19 Ксения
    assert schedule_service.get_duty_info(session, date(2026, 11, 14)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 15)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 16)).curator.id == yulia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 17)).curator.id == yulia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 18)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 19)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 20)).curator.id == yulia.id


def test_cycle_continues_across_month_and_year_boundary(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    """Цикл не сбрасывается на 1-е число месяца/года."""
    _ = seeded_cycle
    # 30.11.2026 = +16 дней от 14.11 → phase 0 → Ксения
    assert schedule_service.get_duty_info(session, date(2026, 11, 30)).curator.id == ksenia.id
    # 01.12.2026 = +17 → phase 1 → Ксения (продолжение пары, не новый месяц)
    assert schedule_service.get_duty_info(session, date(2026, 12, 1)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 12, 2)).curator.id == yulia.id

    # 31.12.2026 = +47 → 47 % 4 = 3 → Юлия
    assert schedule_service.get_duty_info(session, date(2026, 12, 31)).curator.id == yulia.id
    # 01.01.2027 = +48 → phase 0 → Ксения (год не сбрасывает)
    assert schedule_service.get_duty_info(session, date(2027, 1, 1)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2027, 1, 2)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2027, 1, 3)).curator.id == yulia.id


def test_one_off_override_does_not_break_cycle(
    session: Session,
    seeded_cycle: ScheduleCycle,
    ksenia: User,
    yulia: User,
    admin_user: User,
):
    _ = seeded_cycle
    # 18.11 по циклу Ксения → подмена на Юлию
    schedule_service.set_day_override(
        session, on_date=date(2026, 11, 18), curator=yulia, actor=admin_user
    )
    session.commit()

    info = schedule_service.get_duty_info(session, date(2026, 11, 18))
    assert info is not None
    assert info.from_override is True
    assert info.curator.id == yulia.id
    assert info.cycle_curator is not None
    assert info.cycle_curator.id == ksenia.id

    # соседние дни по циклу без изменений
    assert schedule_service.get_duty_info(session, date(2026, 11, 17)).curator.id == yulia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 19)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 20)).curator.id == yulia.id

    # вернуть к циклу
    assert schedule_service.clear_day_override(
        session, on_date=date(2026, 11, 18), actor=admin_user
    )
    session.commit()
    restored = schedule_service.get_duty_info(session, date(2026, 11, 18))
    assert restored is not None
    assert restored.from_override is False
    assert restored.curator.id == ksenia.id


def test_swap_two_days_uses_overrides(
    session: Session,
    seeded_cycle: ScheduleCycle,
    ksenia: User,
    yulia: User,
    admin_user: User,
):
    _ = seeded_cycle
    # 14 Ксения, 16 Юлия → обмен
    a, b = schedule_service.swap_two_days(
        session,
        day_a=date(2026, 11, 14),
        day_b=date(2026, 11, 16),
        actor=admin_user,
    )
    session.commit()
    assert a.curator.id == yulia.id
    assert b.curator.id == ksenia.id
    assert a.from_override is True
    assert b.from_override is True
    # цикл на 15 и 17 не тронут
    assert schedule_service.get_duty_info(session, date(2026, 11, 15)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 17)).curator.id == yulia.id


def test_change_cycle_from_date_leaves_past_intact(
    session: Session,
    seeded_cycle: ScheduleCycle,
    ksenia: User,
    yulia: User,
    admin_user: User,
):
    _ = seeded_cycle
    # С 20.11 стартует Юлия (раньше с 20 была Юлия по старому — сделаем Ксению второй)
    # Новый якорь 20.11: first=Юлия → 20-21 Юлия, 22-23 Ксения
    schedule_service.change_cycle_from_date(
        session,
        effective_from=date(2026, 11, 20),
        first_curator=yulia,
        second_curator=ksenia,
        actor=admin_user,
    )
    session.commit()

    # до даты — старый цикл
    assert schedule_service.get_duty_info(session, date(2026, 11, 18)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 19)).curator.id == ksenia.id
    # с даты — новый
    assert schedule_service.get_duty_info(session, date(2026, 11, 20)).curator.id == yulia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 21)).curator.id == yulia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 22)).curator.id == ksenia.id
    assert schedule_service.get_duty_info(session, date(2026, 11, 23)).curator.id == ksenia.id


def test_timezone_from_settings_not_host(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User
):
    """school_today берёт TIMEZONE из Settings, не локаль машины."""
    _ = (seeded_cycle, ksenia)
    row = session.query(SettingsRecord).filter_by(key="timezone").one()
    row.value = "Asia/Vladivostok"
    session.commit()

    assert schedule_service.get_school_timezone_name(session) == "Asia/Vladivostok"
    tz = schedule_service.get_school_tz(session)
    assert tz == ZoneInfo("Asia/Vladivostok")
    today = schedule_service.school_today(session)
    assert today == datetime.now(ZoneInfo("Asia/Vladivostok")).date()
    # не сравниваем с «голым» date.today() — на границе суток они могут разойтись


def test_work_hours_defaults(session: Session):
    start, end = schedule_service.get_work_hours(session)
    assert start.hour == 8 and start.minute == 0
    assert end.hour == 21 and end.minute == 0


def test_get_duty_curator_uses_schedule(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    _ = seeded_cycle
    assert get_duty_curator(session, date(2026, 11, 14)).id == ksenia.id
    assert get_duty_curator(session, date(2026, 11, 16)).id == yulia.id

    schedule_service.set_day_override(
        session, on_date=date(2026, 11, 14), curator=yulia
    )
    session.commit()
    assert get_duty_curator(session, date(2026, 11, 14)).id == yulia.id


def test_resolve_assignee_senior_gets_duty(
    session: Session,
    seeded_cycle: ScheduleCycle,
    ksenia: User,
    yulia: User,
    senior_user: User,
):
    _ = (seeded_cycle, yulia)
    # «сегодня» школы при Europe/Moscow в фикстуре — реальная дата прогона
    duty = get_duty_curator(session, None)
    assert duty is not None
    resolved = resolve_assignee_for_new_ticket(session, senior_user)
    assert resolved is not None
    assert resolved.id == duty.id
    # куратор по-прежнему себе
    assert resolve_assignee_for_new_ticket(session, ksenia).id == ksenia.id


def test_month_map_and_calendar_text(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    _ = (seeded_cycle, ksenia, yulia)
    mapping = schedule_service.month_duty_map(session, 2026, 11)
    assert date(2026, 11, 14) in mapping
    assert mapping[date(2026, 11, 14)].curator.id == ksenia.id
    text = schedule_service.format_month_calendar(session, 2026, 11)
    assert "Ноябрь 2026" in text
    assert "Europe/Moscow" in text
    assert "08:00" in text and "21:00" in text


def test_shift_month_across_year():
    assert schedule_service.shift_month(2026, 12, 1) == (2027, 1)
    assert schedule_service.shift_month(2027, 1, -1) == (2026, 12)


def test_manage_hub_and_cycle_summary(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    _ = seeded_cycle
    hub = schedule_service.format_manage_hub(session)
    assert "График дежурного" in hub
    assert "Ксения" in hub and "Юлия" in hub
    assert "Цикл 2/2" in hub

    summary = schedule_service.format_cycle_summary(
        session, date(2026, 11, 19), html=False
    )
    assert "Ксения" in summary
    assert "Юлия" in summary


def test_day_manage_keyboard_hides_reset_without_override(
    session: Session, seeded_cycle: ScheduleCycle, ksenia: User, yulia: User
):
    from bot.keyboards.schedule import day_manage_keyboard, manage_hub_keyboard

    _ = seeded_cycle
    on_date = date(2026, 11, 18)
    kb = day_manage_keyboard(
        on_date,
        [ksenia, yulia],
        current_id=ksenia.id,
        has_override=False,
    )
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(t.startswith("● Сейчас дежурит") for t in texts)
    assert any(t.startswith("Назначить:") for t in texts)
    assert not any("Убрать подмену" in (t or "") for t in texts)

    kb2 = day_manage_keyboard(
        on_date,
        [ksenia, yulia],
        current_id=yulia.id,
        has_override=True,
    )
    texts2 = [btn.text for row in kb2.inline_keyboard for btn in row]
    assert any("Убрать подмену" in (t or "") for t in texts2)

    hub_kb = manage_hub_keyboard()
    hub_texts = [btn.text for row in hub_kb.inline_keyboard for btn in row]
    assert "📅 Открыть календарь" in hub_texts
    assert "👤 Назначить дежурного на день" in hub_texts
    assert "🔄 Поменять два дня местами" in hub_texts
    assert "📐 Задать цикл 2/2 с даты" in hub_texts


def test_override_persists_in_db(
    session: Session,
    seeded_cycle: ScheduleCycle,
    yulia: User,
    admin_user: User,
):
    _ = seeded_cycle
    schedule_service.set_day_override(
        session, on_date=date(2026, 11, 18), curator=yulia, actor=admin_user
    )
    session.commit()
    row = (
        session.query(ScheduleDayOverride)
        .filter_by(day=date(2026, 11, 18))
        .one()
    )
    assert row.curator_id == yulia.id
