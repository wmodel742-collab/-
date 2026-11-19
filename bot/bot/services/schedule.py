"""График дежурств кураторов 2/2 (этап 5)."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    ScheduleCycle,
    ScheduleDayOverride,
    SettingsRecord,
    User,
    UserRole,
)
from bot.services.users import write_audit

# Точка отсчёта из ТЗ: 14–15.11.2026 Ксения, 16–17 Юлия, далее непрерывно
DEFAULT_ANCHOR_DATE = date(2026, 11, 14)
DEFAULT_FIRST_CURATOR_NAME = "Ксения"
DEFAULT_SECOND_CURATOR_NAME = "Юлия"
DEFAULT_WORK_START = "08:00"
DEFAULT_WORK_END = "21:00"
DEFAULT_TIMEZONE = "Europe/Moscow"

SETTING_TIMEZONE = "timezone"
SETTING_WORK_START = "work_hours_start"
SETTING_WORK_END = "work_hours_end"

MONTH_NAMES_RU = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


@dataclass(frozen=True, slots=True)
class DutyInfo:
    """Дежурный на дату с источником назначения."""

    day: date
    curator: User
    from_override: bool
    cycle_curator: User | None = None


def get_setting(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.query(SettingsRecord).filter_by(key=key).one_or_none()
    if row is None:
        return default
    return row.value


def set_setting(session: Session, key: str, value: str) -> SettingsRecord:
    row = session.query(SettingsRecord).filter_by(key=key).one_or_none()
    if row is None:
        row = SettingsRecord(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()
    return row


def seed_schedule_settings(session: Session, timezone: str | None = None) -> None:
    """Рабочие часы и часовой пояс школы (не системные часы хоста)."""
    defaults = {
        SETTING_WORK_START: DEFAULT_WORK_START,
        SETTING_WORK_END: DEFAULT_WORK_END,
    }
    if timezone:
        defaults[SETTING_TIMEZONE] = timezone
    for key, value in defaults.items():
        existing = session.query(SettingsRecord).filter_by(key=key).one_or_none()
        if existing is None:
            session.add(SettingsRecord(key=key, value=value))
        elif key == SETTING_TIMEZONE and timezone and existing.value != timezone:
            existing.value = timezone
    session.flush()


def get_school_timezone_name(session: Session) -> str:
    """Часовой пояс только из Settings / .env (через seed), не с компьютера."""
    value = get_setting(session, SETTING_TIMEZONE, DEFAULT_TIMEZONE)
    return (value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def get_school_tz(session: Session) -> ZoneInfo:
    name = get_school_timezone_name(session)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def school_now(session: Session) -> datetime:
    return datetime.now(get_school_tz(session))


def school_today(session: Session) -> date:
    return school_now(session).date()


def get_work_hours(session: Session) -> tuple[time, time]:
    start_raw = get_setting(session, SETTING_WORK_START, DEFAULT_WORK_START) or DEFAULT_WORK_START
    end_raw = get_setting(session, SETTING_WORK_END, DEFAULT_WORK_END) or DEFAULT_WORK_END
    return _parse_hhmm(start_raw), _parse_hhmm(end_raw)


def _parse_hhmm(raw: str) -> time:
    parts = raw.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour=hour, minute=minute)


def short_name(user: User) -> str:
    first = (user.name or "").strip().split()[0] if user.name else "?"
    return first[:2] if len(first) >= 2 else first


def _name_matches(user_name: str, expected: str) -> bool:
    left = (user_name or "").strip().casefold()
    right = expected.strip().casefold()
    if not left or not right:
        return False
    first = left.split()[0]
    return first == right or left.startswith(right)


def find_curator_by_name(session: Session, name: str) -> User | None:
    curators = (
        session.query(User)
        .filter(User.role == UserRole.CURATOR, User.active.is_(True))
        .order_by(User.id.asc())
        .all()
    )
    for user in curators:
        if _name_matches(user.name, name):
            return user
    return None


def list_active_curators(session: Session) -> list[User]:
    return (
        session.query(User)
        .filter(User.role == UserRole.CURATOR, User.active.is_(True))
        .order_by(User.name.asc(), User.id.asc())
        .all()
    )


def ensure_default_cycle(
    session: Session,
    *,
    actor_user_id: int | None = None,
) -> ScheduleCycle | None:
    """Создаёт стартовый цикл 2/2, если кураторы Ксения/Юлия есть, а цикла ещё нет."""
    existing = session.query(ScheduleCycle).order_by(ScheduleCycle.id.asc()).first()
    if existing is not None:
        return existing

    first = find_curator_by_name(session, DEFAULT_FIRST_CURATOR_NAME)
    second = find_curator_by_name(session, DEFAULT_SECOND_CURATOR_NAME)
    if first is None or second is None:
        return None
    if first.id == second.id:
        return None

    cycle = ScheduleCycle(
        effective_from=DEFAULT_ANCHOR_DATE,
        anchor_date=DEFAULT_ANCHOR_DATE,
        first_curator_id=first.id,
        second_curator_id=second.id,
        created_by_user_id=actor_user_id,
        note="Стартовый цикл 2/2: Ксения → Юлия от 14.11.2026",
    )
    session.add(cycle)
    session.flush()
    write_audit(
        session,
        actor_telegram_id=None,
        action="schedule_cycle_seeded",
        details=(
            f"from={DEFAULT_ANCHOR_DATE.isoformat()}; "
            f"first={first.id}; second={second.id}"
        ),
    )
    return cycle


def get_cycle_for_date(session: Session, on_date: date) -> ScheduleCycle | None:
    ensure_default_cycle(session)
    return (
        session.query(ScheduleCycle)
        .options(
            joinedload(ScheduleCycle.first_curator),
            joinedload(ScheduleCycle.second_curator),
        )
        .filter(ScheduleCycle.effective_from <= on_date)
        .order_by(ScheduleCycle.effective_from.desc(), ScheduleCycle.id.desc())
        .first()
    )


def cycle_curator_for_date(cycle: ScheduleCycle, on_date: date) -> User:
    """Кто по циклу 2/2 на дату (без учёта разовых подмен)."""
    delta = (on_date - cycle.anchor_date).days
    phase = delta % 4
    if phase in (0, 1):
        return cycle.first_curator
    return cycle.second_curator


def get_override(session: Session, on_date: date) -> ScheduleDayOverride | None:
    return (
        session.query(ScheduleDayOverride)
        .options(joinedload(ScheduleDayOverride.curator))
        .filter(ScheduleDayOverride.day == on_date)
        .one_or_none()
    )


def get_duty_info(session: Session, on_date: date) -> DutyInfo | None:
    """Дежурный на дату: override → цикл → None."""
    ensure_default_cycle(session)
    override = get_override(session, on_date)
    cycle = get_cycle_for_date(session, on_date)
    cycle_user = cycle_curator_for_date(cycle, on_date) if cycle is not None else None

    if override is not None:
        return DutyInfo(
            day=on_date,
            curator=override.curator,
            from_override=True,
            cycle_curator=cycle_user,
        )
    if cycle_user is not None:
        return DutyInfo(
            day=on_date,
            curator=cycle_user,
            from_override=False,
            cycle_curator=cycle_user,
        )
    return None


def set_day_override(
    session: Session,
    *,
    on_date: date,
    curator: User,
    actor: User | None = None,
    note: str | None = None,
) -> ScheduleDayOverride:
    if curator.role != UserRole.CURATOR or not curator.active:
        raise ValueError("Дежурным можно назначить только активного куратора.")

    row = get_override(session, on_date)
    if row is None:
        row = ScheduleDayOverride(
            day=on_date,
            curator_id=curator.id,
            created_by_user_id=actor.id if actor else None,
            note=note,
        )
        session.add(row)
    else:
        row.curator_id = curator.id
        row.note = note
        row.created_by_user_id = actor.id if actor else row.created_by_user_id
    session.flush()
    session.refresh(row)
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id if actor else None,
        action="schedule_day_override_set",
        details=f"day={on_date.isoformat()}; curator_id={curator.id}",
    )
    from bot.services.handoff import immediate_duty_handoff_if_today

    immediate_duty_handoff_if_today(
        session,
        on_date=on_date,
        actor_telegram_id=actor.telegram_id if actor else None,
    )
    return row


def clear_day_override(
    session: Session,
    *,
    on_date: date,
    actor: User | None = None,
) -> bool:
    row = get_override(session, on_date)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id if actor else None,
        action="schedule_day_override_cleared",
        details=f"day={on_date.isoformat()}",
    )
    from bot.services.handoff import immediate_duty_handoff_if_today

    immediate_duty_handoff_if_today(
        session,
        on_date=on_date,
        actor_telegram_id=actor.telegram_id if actor else None,
    )
    return True


def swap_two_days(
    session: Session,
    *,
    day_a: date,
    day_b: date,
    actor: User | None = None,
) -> tuple[DutyInfo, DutyInfo]:
    """Меняет дежурных двух дней местами через разовые подмены (цикл не трогаем)."""
    if day_a == day_b:
        raise ValueError("Выберите два разных дня.")

    info_a = get_duty_info(session, day_a)
    info_b = get_duty_info(session, day_b)
    if info_a is None or info_b is None:
        raise ValueError("Не удалось определить дежурных для обмена — настройте цикл.")

    curator_a = info_a.curator
    curator_b = info_b.curator

    # Если после обмена день совпадает с циклом — снимаем override
    cycle_a = info_a.cycle_curator
    cycle_b = info_b.cycle_curator

    if cycle_a is not None and curator_b.id == cycle_a.id:
        clear_day_override(session, on_date=day_a, actor=actor)
    else:
        set_day_override(
            session,
            on_date=day_a,
            curator=curator_b,
            actor=actor,
            note=f"Обмен со сменой {day_b.isoformat()}",
        )

    if cycle_b is not None and curator_a.id == cycle_b.id:
        clear_day_override(session, on_date=day_b, actor=actor)
    else:
        set_day_override(
            session,
            on_date=day_b,
            curator=curator_a,
            actor=actor,
            note=f"Обмен со сменой {day_a.isoformat()}",
        )

    write_audit(
        session,
        actor_telegram_id=actor.telegram_id if actor else None,
        action="schedule_days_swapped",
        details=f"a={day_a.isoformat()}; b={day_b.isoformat()}",
    )
    from bot.services.handoff import immediate_duty_handoff_if_today

    today = school_today(session)
    if day_a == today or day_b == today:
        immediate_duty_handoff_if_today(
            session,
            on_date=today,
            actor_telegram_id=actor.telegram_id if actor else None,
        )
    result_a = get_duty_info(session, day_a)
    result_b = get_duty_info(session, day_b)
    assert result_a is not None and result_b is not None
    return result_a, result_b


def change_cycle_from_date(
    session: Session,
    *,
    effective_from: date,
    first_curator: User,
    second_curator: User,
    actor: User | None = None,
    note: str | None = None,
) -> ScheduleCycle:
    """Новый основной цикл с даты: фаза якорится на effective_from (2/2)."""
    if first_curator.id == second_curator.id:
        raise ValueError("В цикле нужны два разных куратора.")
    for user in (first_curator, second_curator):
        if user.role != UserRole.CURATOR or not user.active:
            raise ValueError("В цикле могут быть только активные кураторы.")

    cycle = ScheduleCycle(
        effective_from=effective_from,
        anchor_date=effective_from,
        first_curator_id=first_curator.id,
        second_curator_id=second_curator.id,
        created_by_user_id=actor.id if actor else None,
        note=note or f"Новый цикл с {effective_from.strftime('%d.%m.%Y')}",
    )
    session.add(cycle)
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor.telegram_id if actor else None,
        action="schedule_cycle_changed",
        details=(
            f"from={effective_from.isoformat()}; "
            f"first={first_curator.id}; second={second_curator.id}"
        ),
    )
    return cycle


def month_duty_map(
    session: Session, year: int, month: int
) -> dict[date, DutyInfo]:
    """Карта дежурств за месяц (включая переходы через границы месяцев/годов)."""
    _, last_day = calendar.monthrange(year, month)
    result: dict[date, DutyInfo] = {}
    for day_num in range(1, last_day + 1):
        d = date(year, month, day_num)
        info = get_duty_info(session, d)
        if info is not None:
            result[d] = info
    return result


def format_cycle_summary(
    session: Session,
    on_date: date | None = None,
    *,
    html: bool = True,
) -> str:
    """Кратко: текущая пара цикла и кто дежурит сегодня."""
    day = on_date if on_date is not None else school_today(session)
    cycle = get_cycle_for_date(session, day)
    today_info = get_duty_info(session, day)
    b, be = ("<b>", "</b>") if html else ("", "")
    lines: list[str] = []
    if cycle is not None:
        first = cycle.first_curator.name if cycle.first_curator else "?"
        second = cycle.second_curator.name if cycle.second_curator else "?"
        lines.append(
            f"Цикл 2/2 с {cycle.effective_from.strftime('%d.%m.%Y')}: "
            f"{b}{first}{be} (2 дня) → {b}{second}{be} (2 дня) → снова…"
        )
    else:
        lines.append(
            "Цикл 2/2 ещё не задан. Добавьте двух кураторов "
            "(например Ксения и Юлия) или нажмите «Задать цикл 2/2»."
        )
    if today_info:
        src = "разовая подмена" if today_info.from_override else "по циклу"
        lines.append(
            f"Сегодня ({day.strftime('%d.%m.%Y')}): "
            f"{b}{today_info.curator.name}{be} — {src}"
        )
    else:
        lines.append(f"Сегодня ({day.strftime('%d.%m.%Y')}): дежурный не задан.")
    return "\n".join(lines)


def format_manage_hub(session: Session) -> str:
    """Стартовый экран настройки графика дежурного для админа."""
    return (
        "🗓 <b>График дежурного (менеджера)</b>\n\n"
        f"{format_cycle_summary(session)}\n\n"
        "Выберите действие ниже. Календарь можно открыть отдельно — "
        "там видно, кто дежурит в каждом дне (* = разовая подмена)."
    )


def format_month_calendar(
    session: Session,
    year: int,
    month: int,
    *,
    manage: bool = False,
) -> str:
    """Текстовый календарь месяца с инициалами дежурных."""
    tz_name = get_school_timezone_name(session)
    work_start, work_end = get_work_hours(session)
    today = school_today(session)
    duties = month_duty_map(session, year, month)

    title = "📅 График дежурного" if manage else "📅 График кураторов"
    lines = [
        f"{title} — {MONTH_NAMES_RU[month]} {year}",
        f"Часовой пояс: {tz_name} · смена {work_start.strftime('%H:%M')}–{work_end.strftime('%H:%M')}",
        "",
        "Пн Вт Ср Чт Пт Сб Вс",
    ]

    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    for week in weeks:
        cells: list[str] = []
        for day in week:
            if day.month != month:
                cells.append("·")
                continue
            info = duties.get(day)
            mark = short_name(info.curator) if info else "—"
            if info and info.from_override:
                mark = f"{mark}*"
            if day == today:
                cells.append(f"[{day.day:02d}{mark}]")
            else:
                cells.append(f"{day.day:02d}{mark}")
        lines.append(" ".join(f"{c:>6}" for c in cells))

    lines.append("")
    lines.append(format_cycle_summary(session, today, html=False))
    lines.append("* — разовая подмена (цикл 2/2 не сбивается)")
    if manage:
        lines.extend(
            [
                "",
                "Нажмите день в кнопках ниже, чтобы назначить дежурного.",
                "Или вернитесь «К действиям» для обмена дней / нового цикла.",
            ]
        )
    return "\n".join(lines)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    month_index = year * 12 + (month - 1) + delta
    new_year, new_month0 = divmod(month_index, 12)
    return new_year, new_month0 + 1
