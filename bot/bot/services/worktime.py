"""Рабочие часы школы: перенос напоминаний с остатком времени."""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo


def combine_local(day: date, hhmm: time, tz: ZoneInfo) -> datetime:
    return datetime(
        day.year, day.month, day.day, hhmm.hour, hhmm.minute, hhmm.second, tzinfo=tz
    )


def is_within_work_hours(moment: datetime, start: time, end: time) -> bool:
    """Момент внутри [start, end) в локальной дате moment."""
    local = moment
    if local.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    t = local.timetz().replace(tzinfo=None)
    return start <= t < end


def next_work_start(moment: datetime, start: time, end: time) -> datetime:
    """Ближайшее начало рабочего периода (сегодня, если ещё не начался, иначе завтра)."""
    tz = moment.tzinfo
    assert tz is not None
    today_start = combine_local(moment.date(), start, tz)  # type: ignore[arg-type]
    today_end = combine_local(moment.date(), end, tz)  # type: ignore[arg-type]
    if moment < today_start:
        return today_start
    if moment >= today_end:
        tomorrow = moment.date() + timedelta(days=1)
        return combine_local(tomorrow, start, tz)  # type: ignore[arg-type]
    return moment


def remaining_work_seconds(moment: datetime, start: time, end: time) -> float:
    """Сколько рабочих секунд осталось до конца текущего рабочего дня (0 если вне смены)."""
    if not is_within_work_hours(moment, start, end):
        return 0.0
    tz = moment.tzinfo
    assert tz is not None
    day_end = combine_local(moment.date(), end, tz)  # type: ignore[arg-type]
    return max(0.0, (day_end - moment).total_seconds())


def add_work_hours(
    moment: datetime,
    hours: float,
    start: time,
    end: time,
) -> datetime:
    """Прибавить `hours` рабочих часов, перенося остаток на следующий рабочий период.

    Если `moment` вне рабочих часов — отсчёт начинается с ближайшего начала смены,
    а весь запрошенный интервал сохраняется (не сгорает).
    """
    if hours < 0:
        raise ValueError("hours must be >= 0")
    if moment.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    if start >= end:
        raise ValueError("work start must be before work end")

    remaining = float(hours) * 3600.0
    cursor = next_work_start(moment, start, end)

    # защита от бесконечного цикла
    for _ in range(10_000):
        if remaining <= 1e-9:
            return cursor
        avail = remaining_work_seconds(cursor, start, end)
        if avail <= 1e-9:
            cursor = next_work_start(cursor + timedelta(seconds=1), start, end)
            continue
        if remaining <= avail + 1e-9:
            return cursor + timedelta(seconds=remaining)
        remaining -= avail
        # конец текущего дня → следующий рабочий старт
        tz = cursor.tzinfo
        assert tz is not None
        day_end = combine_local(cursor.date(), end, tz)  # type: ignore[arg-type]
        cursor = next_work_start(day_end + timedelta(seconds=1), start, end)

    raise RuntimeError("add_work_hours: too many iterations")


def clamp_run_at_to_work_hours(
    run_at: datetime,
    start: time,
    end: time,
) -> datetime:
    """Если момент вне смены — перенести на начало следующего рабочего периода."""
    if is_within_work_hours(run_at, start, end):
        return run_at
    return next_work_start(run_at, start, end)
