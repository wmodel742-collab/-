"""Аналитика переносов: периоды, статистика, стабильность, CSV (этап 7)."""

from __future__ import annotations

import csv
import enum
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, time, timezone
from typing import Iterable, Sequence

from sqlalchemy.orm import Session, joinedload

from bot.db.models import (
    TICKET_INITIATOR_LABELS_RU,
    TICKET_STATUS_LABELS_RU,
    TICKET_TYPE_LABELS_RU,
    Ticket,
    TicketInitiator,
    TicketStatus,
    TicketSubtask,
    TicketType,
    User,
    UserRole,
)
from bot.services import schedule as schedule_service

# Типы, которые считаются переносами (не подбор замены)
TRANSFER_TYPES: frozenset[TicketType] = frozenset(
    {TicketType.ONE_TIME, TicketType.PERMANENT, TicketType.MASS}
)

# Инициаторы, не считающиеся нарушением стабильности
NON_STABILITY_INITIATORS: frozenset[TicketInitiator] = frozenset(
    {TicketInitiator.STUDENT_PARENT, TicketInitiator.SCHOOL}
)

STABILITY_WINDOW_DAYS = 30
WARN_LEVEL_CURATOR = 2
WARN_LEVEL_ADMIN = 3


class PeriodKind(str, enum.Enum):
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    CALENDAR_MONTH = "month"
    CUSTOM = "custom"


PERIOD_LABELS_RU: dict[PeriodKind, str] = {
    PeriodKind.DAYS_7: "Последние 7 дней",
    PeriodKind.DAYS_30: "Последние 30 дней",
    PeriodKind.CALENDAR_MONTH: "Календарный месяц",
    PeriodKind.CUSTOM: "Произвольный период",
}


@dataclass(frozen=True, slots=True)
class DateRange:
    start: datetime  # inclusive, tz-aware
    end: datetime  # exclusive, tz-aware
    kind: PeriodKind
    label: str

    @property
    def start_date(self) -> date:
        return self.start.date()

    @property
    def end_date_inclusive(self) -> date:
        return (self.end - timedelta(seconds=1)).date()


@dataclass(frozen=True, slots=True)
class TransferEvent:
    """Одно событие переноса по ученику (заявка или подзадача массовой)."""

    ticket_id: int
    ticket_number: int
    ticket_type: TicketType
    status: TicketStatus
    initiator: TicketInitiator
    teacher_name: str
    student_name: str
    student_external_id: str
    created_at: datetime
    lesson_ref: str | None
    subtask_id: int | None = None

    @property
    def fingerprint(self) -> str:
        """Ключ для дедупликации одинаковых переносов."""
        lesson = (self.lesson_ref or "").strip().lower()
        return (
            f"{self.student_external_id.strip().lower()}|"
            f"{self.teacher_name.strip().lower()}|"
            f"{lesson}|{self.ticket_type.value}"
        )

    @property
    def counts_for_stability(self) -> bool:
        if self.initiator in NON_STABILITY_INITIATORS:
            return False
        if self.initiator != TicketInitiator.TEACHER:
            return False
        if self.status == TicketStatus.REJECTED:
            return False
        return True


@dataclass
class TeacherStats:
    teacher_name: str
    transfer_cases: int = 0  # заявки-переносы (mass = 1 случай)
    absence_cases: int = 0  # mass / temporary_absence как один случай отсутствия
    affected_lessons: int = 0  # занятия (подзадачи + разовые/постоянные)
    unique_students: int = 0
    by_initiator: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    stability_relevant: int = 0  # только teacher-init, без отказ/дублей
    parent_or_school: int = 0
    history: list[TransferEvent] = field(default_factory=list)


@dataclass
class StudentStats:
    student_name: str
    student_external_id: str
    transfer_count: int = 0
    teacher_initiators: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    agreed: int = 0  # DONE
    open_count: int = 0  # NOT_PROCESSED / WAITING_REPLY
    rejected: int = 0
    stability_count_30d: int = 0
    events: list[TransferEvent] = field(default_factory=list)


def school_local_now(session: Session) -> datetime:
    return schedule_service.school_now(session)


def resolve_period(
    session: Session,
    kind: PeriodKind,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    ref: datetime | None = None,
) -> DateRange:
    """Интервал [start, end) в часовом поясе школы."""
    tz = schedule_service.get_school_tz(session)
    now = ref or school_local_now(session)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    today = now.date()

    if kind == PeriodKind.DAYS_7:
        start_d = today - timedelta(days=6)
        end_d = today
        label = PERIOD_LABELS_RU[kind]
    elif kind == PeriodKind.DAYS_30:
        start_d = today - timedelta(days=29)
        end_d = today
        label = PERIOD_LABELS_RU[kind]
    elif kind == PeriodKind.CALENDAR_MONTH:
        start_d = today.replace(day=1)
        if today.month == 12:
            end_d = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_d = date(today.year, today.month + 1, 1) - timedelta(days=1)
        label = f"{schedule_service.MONTH_NAMES_RU[today.month]} {today.year}"
    elif kind == PeriodKind.CUSTOM:
        if start_date is None or end_date is None:
            raise ValueError("Для произвольного периода укажите даты начала и конца.")
        if end_date < start_date:
            raise ValueError("Дата конца раньше даты начала.")
        start_d, end_d = start_date, end_date
        label = f"{start_d.strftime('%d.%m.%Y')}–{end_d.strftime('%d.%m.%Y')}"
    else:
        raise ValueError(f"Неизвестный период: {kind}")

    start = datetime.combine(start_d, time.min, tzinfo=tz)
    end = datetime.combine(end_d + timedelta(days=1), time.min, tzinfo=tz)
    return DateRange(start=start, end=end, kind=kind, label=label)


def rolling_30d_range(session: Session, *, ref: datetime | None = None) -> DateRange:
    return resolve_period(session, PeriodKind.DAYS_30, ref=ref)


def _aware(dt: datetime, tz) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt.astimezone(tz)


def _lesson_ref_for_ticket(ticket: Ticket) -> str | None:
    if ticket.ticket_type == TicketType.ONE_TIME:
        return ticket.original_datetime
    if ticket.ticket_type == TicketType.PERMANENT:
        parts = [
            str(ticket.current_weekday or ""),
            ticket.lesson_time or "",
            ticket.nearest_lesson_date.isoformat() if ticket.nearest_lesson_date else "",
        ]
        return "|".join(parts)
    return ticket.absence_period


def iter_transfer_events(
    session: Session,
    *,
    date_range: DateRange | None = None,
    teacher_name: str | None = None,
    student_external_id: str | None = None,
) -> list[TransferEvent]:
    """Собрать события переноса (разовые/постоянные/подзадачи mass)."""
    tz = schedule_service.get_school_tz(session)
    q = (
        session.query(Ticket)
        .options(joinedload(Ticket.subtasks))
        .filter(Ticket.ticket_type.in_(tuple(TRANSFER_TYPES)))
        .order_by(Ticket.created_at.asc())
    )
    if teacher_name:
        q = q.filter(Ticket.teacher_name == teacher_name.strip())
    tickets = list(q.all())

    events: list[TransferEvent] = []
    for ticket in tickets:
        created = _aware(ticket.created_at, tz)
        if date_range is not None and not (date_range.start <= created < date_range.end):
            continue

        if ticket.ticket_type == TicketType.MASS:
            for sub in ticket.subtasks or []:
                sid = (sub.student_external_id or "").strip()
                if student_external_id and sid != student_external_id.strip():
                    continue
                sub_created = _aware(sub.created_at, tz)
                if date_range is not None and not (
                    date_range.start <= sub_created < date_range.end
                ):
                    # mass parent in range but subtask outside — skip sub
                    continue
                events.append(
                    TransferEvent(
                        ticket_id=ticket.id,
                        ticket_number=ticket.number,
                        ticket_type=ticket.ticket_type,
                        status=sub.status,
                        initiator=ticket.initiator,
                        teacher_name=ticket.teacher_name,
                        student_name=sub.student_name,
                        student_external_id=sid,
                        created_at=sub_created,
                        lesson_ref=sub.lesson_datetime,
                        subtask_id=sub.id,
                    )
                )
            continue

        sid = (ticket.student_external_id or "").strip()
        if student_external_id and sid != student_external_id.strip():
            continue
        if not sid:
            continue
        events.append(
            TransferEvent(
                ticket_id=ticket.id,
                ticket_number=ticket.number,
                ticket_type=ticket.ticket_type,
                status=ticket.status,
                initiator=ticket.initiator,
                teacher_name=ticket.teacher_name,
                student_name=ticket.student_name or "",
                student_external_id=sid,
                created_at=created,
                lesson_ref=_lesson_ref_for_ticket(ticket),
                subtask_id=None,
            )
        )
    return events


def dedupe_stability_events(events: Sequence[TransferEvent]) -> list[TransferEvent]:
    """Уникальные события стабильности: отказ и дубли отбрасываются."""
    seen: set[str] = set()
    result: list[TransferEvent] = []
    for ev in events:
        if not ev.counts_for_stability:
            continue
        fp = ev.fingerprint
        if fp in seen:
            continue
        seen.add(fp)
        result.append(ev)
    return result


def count_stability_transfers_for_student(
    session: Session,
    *,
    student_external_id: str,
    ref: datetime | None = None,
    exclude_ticket_id: int | None = None,
) -> int:
    """Число переносов по инициативе преподавателя за скользящие 30 дней."""
    window = rolling_30d_range(session, ref=ref)
    events = iter_transfer_events(
        session, date_range=window, student_external_id=student_external_id
    )
    if exclude_ticket_id is not None:
        events = [e for e in events if e.ticket_id != exclude_ticket_id]
    return len(dedupe_stability_events(events))


def list_teacher_names(session: Session, *, date_range: DateRange | None = None) -> list[str]:
    events = iter_transfer_events(session, date_range=date_range)
    names = sorted({e.teacher_name for e in events}, key=lambda s: s.casefold())
    # также преподаватели из заявок на замену (для полноты списка)
    q = session.query(Ticket.teacher_name).distinct().order_by(Ticket.teacher_name.asc())
    extra = [row[0] for row in q.all() if row[0]]
    merged = sorted(set(names) | set(extra), key=lambda s: s.casefold())
    return merged


def teacher_stats(
    session: Session,
    *,
    teacher_name: str,
    date_range: DateRange,
) -> TeacherStats:
    name = teacher_name.strip()
    events = iter_transfer_events(session, date_range=date_range, teacher_name=name)
    stats = TeacherStats(teacher_name=name, history=list(reversed(events)))

    # случаи переноса: уникальные ticket_id среди transfer events
    case_ids = {e.ticket_id for e in events}
    stats.transfer_cases = len(case_ids)

    # затронутые занятия = число событий (подзадача / разовый / постоянный)
    stats.affected_lessons = len(events)

    # случаи отсутствия: mass-заявки + temporary_absence в периоде
    tz = schedule_service.get_school_tz(session)
    mass_ids = {
        e.ticket_id for e in events if e.ticket_type == TicketType.MASS
    }
    absence_q = (
        session.query(Ticket)
        .filter(
            Ticket.teacher_name == name,
            Ticket.ticket_type.in_(
                (TicketType.TEMPORARY_ABSENCE, TicketType.DISMISSAL, TicketType.MASS)
            ),
        )
        .all()
    )
    absence_cases = 0
    for t in absence_q:
        created = _aware(t.created_at, tz)
        if date_range.start <= created < date_range.end:
            absence_cases += 1
    # mass уже в absence_q; не дублируем
    stats.absence_cases = absence_cases or len(mass_ids)

    stats.unique_students = len({e.student_external_id for e in events if e.student_external_id})

    by_init: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    parent_school = 0
    for e in events:
        by_init[e.initiator.value] += 1
        by_status[e.status.value] += 1
        if e.initiator in NON_STABILITY_INITIATORS:
            parent_school += 1
    stats.by_initiator = dict(by_init)
    stats.by_status = dict(by_status)
    stats.parent_or_school = parent_school
    stats.stability_relevant = len(dedupe_stability_events(events))
    return stats


def student_stats_list(
    session: Session,
    *,
    date_range: DateRange | None = None,
    sort_by_count: bool = True,
) -> list[StudentStats]:
    """Сводка по ученикам за период (по умолчанию 30 дней)."""
    rng = date_range or rolling_30d_range(session)
    events = iter_transfer_events(session, date_range=rng)
    by_student: dict[str, list[TransferEvent]] = defaultdict(list)
    for e in events:
        if not e.student_external_id:
            continue
        by_student[e.student_external_id].append(e)

    result: list[StudentStats] = []
    for sid, evs in by_student.items():
        evs_sorted = sorted(evs, key=lambda x: x.created_at)
        teachers = []
        seen_t: set[str] = set()
        for e in evs_sorted:
            if e.initiator == TicketInitiator.TEACHER:
                key = e.teacher_name.casefold()
                if key not in seen_t:
                    seen_t.add(key)
                    teachers.append(e.teacher_name)
        name = evs_sorted[-1].student_name
        dates = sorted({e.created_at.date().strftime("%d.%m.%Y") for e in evs_sorted})
        agreed = sum(1 for e in evs_sorted if e.status == TicketStatus.DONE)
        open_c = sum(
            1
            for e in evs_sorted
            if e.status in (TicketStatus.NOT_PROCESSED, TicketStatus.WAITING_REPLY)
        )
        rejected = sum(1 for e in evs_sorted if e.status == TicketStatus.REJECTED)
        stability = len(dedupe_stability_events(evs_sorted))
        result.append(
            StudentStats(
                student_name=name,
                student_external_id=sid,
                transfer_count=len(evs_sorted),
                teacher_initiators=teachers,
                dates=dates,
                agreed=agreed,
                open_count=open_c,
                rejected=rejected,
                stability_count_30d=stability,
                events=list(reversed(evs_sorted)),
            )
        )

    if sort_by_count:
        result.sort(
            key=lambda s: (-s.transfer_count, s.student_name.casefold(), s.student_external_id)
        )
    else:
        result.sort(key=lambda s: (s.student_name.casefold(), s.student_external_id))
    return result


def format_teacher_stats(stats: TeacherStats, *, date_range: DateRange) -> str:
    lines = [
        f"📊 Преподаватель: <b>{stats.teacher_name}</b>",
        f"Период: {date_range.label}",
        "",
        f"Случаи переноса: <b>{stats.transfer_cases}</b>",
        f"Случаи отсутствия (1 заявка = 1 случай): <b>{stats.absence_cases}</b>",
        f"Затронутые занятия: <b>{stats.affected_lessons}</b>",
        f"Уникальные ученики: <b>{stats.unique_students}</b>",
        "",
        "По инициаторам:",
    ]
    if stats.by_initiator:
        for key, cnt in sorted(stats.by_initiator.items()):
            label = TICKET_INITIATOR_LABELS_RU.get(
                TicketInitiator(key), key
            )
            note = ""
            if key in {i.value for i in NON_STABILITY_INITIATORS}:
                note = " (не нарушение стабильности)"
            lines.append(f"• {label}: {cnt}{note}")
    else:
        lines.append("• нет данных")

    lines.append("")
    lines.append("По статусам:")
    if stats.by_status:
        for key, cnt in sorted(stats.by_status.items()):
            label = TICKET_STATUS_LABELS_RU.get(TicketStatus(key), key)
            lines.append(f"• {label}: {cnt}")
    else:
        lines.append("• нет данных")

    lines.append("")
    lines.append(
        f"Учитывается в стабильности (преподаватель, без отказов/дублей): "
        f"<b>{stats.stability_relevant}</b>"
    )
    lines.append(
        f"Родитель / школа (не нарушение): <b>{stats.parent_or_school}</b>"
    )

    if stats.history:
        lines.append("")
        lines.append("<b>История</b> (последние 15):")
        for e in stats.history[:15]:
            when = e.created_at.strftime("%d.%m.%Y")
            typ = TICKET_TYPE_LABELS_RU.get(e.ticket_type, e.ticket_type.value)
            st = TICKET_STATUS_LABELS_RU.get(e.status, e.status.value)
            init = TICKET_INITIATOR_LABELS_RU.get(e.initiator, e.initiator.value)
            lines.append(
                f"• #{e.ticket_number} {when} — {e.student_name}, "
                f"{typ}, {init}, {st}"
            )
    return "\n".join(lines)


def format_students_stats(
    items: Sequence[StudentStats], *, date_range: DateRange, limit: int = 30
) -> str:
    lines = [
        "📊 <b>Статистика по ученикам</b>",
        f"Период: {date_range.label}",
        f"Учеников с переносами: <b>{len(items)}</b>",
        "",
    ]
    if not items:
        lines.append("Нет переносов за период.")
        return "\n".join(lines)

    for s in items[:limit]:
        teachers = ", ".join(s.teacher_initiators) if s.teacher_initiators else "—"
        dates = ", ".join(s.dates[:5]) + ("…" if len(s.dates) > 5 else "")
        warn = ""
        if s.stability_count_30d >= WARN_LEVEL_ADMIN:
            warn = " 🚨"
        elif s.stability_count_30d >= WARN_LEVEL_CURATOR:
            warn = " ⚠️"
        lines.append(
            f"• <b>{s.student_name}</b> — "
            f"переносов: <b>{s.transfer_count}</b>{warn}\n"
            f"  преподаватели-инициаторы: {teachers}\n"
            f"  даты: {dates or '—'}\n"
            f"  согласовано: {s.agreed} / открыто: {s.open_count}"
            + (f" / отказ: {s.rejected}" if s.rejected else "")
        )
        lines.append("")
    if len(items) > limit:
        lines.append(f"… и ещё {len(items) - limit}")
    return "\n".join(lines).rstrip()


def format_weekly_summary(session: Session, *, ref: datetime | None = None) -> str:
    """Краткий еженедельный отчёт админу (понедельник)."""
    week = resolve_period(session, PeriodKind.DAYS_7, ref=ref)
    month30 = rolling_30d_range(session, ref=ref)
    events_week = iter_transfer_events(session, date_range=week)
    students30 = student_stats_list(session, date_range=month30)
    warn2 = [s for s in students30 if s.stability_count_30d == WARN_LEVEL_CURATOR]
    warn3 = [s for s in students30 if s.stability_count_30d >= WARN_LEVEL_ADMIN]
    teachers = {e.teacher_name for e in events_week}

    lines = [
        "🥑 <b>Еженедельный отчёт АвоПеренос</b>",
        f"Период: {week.label}",
        "",
        f"Переносов (занятий) за неделю: <b>{len(events_week)}</b>",
        f"Преподавателей затронуто: <b>{len(teachers)}</b>",
        f"Учеников с переносами за 30 дней: <b>{len(students30)}</b>",
        f"Предупреждения (2 за 30 дн.): <b>{len(warn2)}</b>",
        f"Эскалации (3+ за 30 дн.): <b>{len(warn3)}</b>",
    ]
    if warn3:
        lines.append("")
        lines.append("3+ переноса (стабильность):")
        for s in warn3[:10]:
            lines.append(f"• {s.student_name} — {s.stability_count_30d}")
    if warn2:
        lines.append("")
        lines.append("2 переноса (внимание куратору):")
        for s in warn2[:10]:
            lines.append(f"• {s.student_name} — {s.stability_count_30d}")
    lines.append("")
    lines.append("Откройте «Статистика» для подробностей и выгрузки CSV.")
    return "\n".join(lines)


def build_transfers_csv(
    session: Session, *, date_range: DateRange, teacher_name: str | None = None
) -> bytes:
    """CSV-выгрузка событий переноса (Excel открывает как таблицу)."""
    events = iter_transfer_events(
        session, date_range=date_range, teacher_name=teacher_name
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "ticket_number",
            "created_at",
            "ticket_type",
            "status",
            "initiator",
            "teacher_name",
            "student_name",
            "student_external_id",
            "lesson_ref",
            "subtask_id",
            "stability_relevant",
        ]
    )
    for e in events:
        writer.writerow(
            [
                e.ticket_number,
                e.created_at.isoformat(timespec="minutes"),
                e.ticket_type.value,
                e.status.value,
                e.initiator.value,
                e.teacher_name,
                e.student_name,
                e.student_external_id,
                e.lesson_ref or "",
                e.subtask_id or "",
                "yes" if e.counts_for_stability else "no",
            ]
        )
    # Excel-friendly BOM
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_students_csv(session: Session, *, date_range: DateRange) -> bytes:
    items = student_stats_list(session, date_range=date_range)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "student_name",
            "student_external_id",
            "transfer_count",
            "stability_count",
            "teacher_initiators",
            "dates",
            "agreed",
            "open",
            "rejected",
        ]
    )
    for s in items:
        writer.writerow(
            [
                s.student_name,
                s.student_external_id,
                s.transfer_count,
                s.stability_count_30d,
                ", ".join(s.teacher_initiators),
                ", ".join(s.dates),
                s.agreed,
                s.open_count,
                s.rejected,
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def list_admins(session: Session) -> list[User]:
    return (
        session.query(User)
        .filter(User.role == UserRole.ADMIN, User.active.is_(True))
        .order_by(User.id.asc())
        .all()
    )


def parse_date_ru(raw: str) -> date:
    text = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Дата: ДД.ММ.ГГГГ")
