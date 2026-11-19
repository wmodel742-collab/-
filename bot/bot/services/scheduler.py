"""APScheduler: cron смены/сводок, сканер срочности, jobstore в SQLite."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import sessionmaker

from bot.db.models import Ticket, TicketSubtask, User
from bot.keyboards.tickets import TicketCB
from bot.services.automation_settings import load_automation_config
from bot.services.handoff import (
    format_evening_digest,
    format_morning_message,
    run_morning_duty_handoff,
)
from bot.services.jobs import (
    JOB_DUTY_EVENING,
    JOB_DUTY_MORNING,
    JOB_URGENCY_SCAN,
    JOB_WEEKLY_STATS,
    get_job_gateway,
)
from bot.services.analytics import format_weekly_summary, list_admins
from bot.keyboards.stats import weekly_keyboard
from bot.services import reminders
from bot.services import schedule as schedule_service
from bot.services.urgency import (
    can_send_urgency,
    format_urgency_message,
    list_urgent_tickets,
    record_notification,
    urgency_target_key,
)
from bot.services.duty import get_duty_curator

logger = logging.getLogger("avoperenos.scheduler")

_scheduler: AsyncIOScheduler | None = None
_session_factory: sessionmaker | None = None
_bot: Bot | None = None


def open_tasks_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Открыть задачи",
                    callback_data=f"{TicketCB.PREFIX}auto:active",
                )
            ]
        ]
    )


def _parse_hhmm(raw: str) -> tuple[int, int]:
    parts = raw.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


async def _send_user(user: User, text: str, reply_markup=None) -> None:
    if _bot is None:
        logger.info("notify(skip bot): to=%s %s", user.telegram_id, text[:80])
        return
    try:
        await _bot.send_message(
            chat_id=user.telegram_id, text=text, reply_markup=reply_markup
        )
    except Exception:
        logger.exception("Failed to DM user %s", user.telegram_id)


async def _send_chat(chat_id: int, text: str) -> None:
    if _bot is None:
        logger.info("notify(skip bot): chat=%s %s", chat_id, text[:80])
        return
    try:
        await _bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logger.exception("Failed to send work chat %s", chat_id)


async def job_morning_handoff() -> None:
    if _session_factory is None:
        return
    with _session_factory() as session:
        result = run_morning_duty_handoff(session)
        session.commit()
        duty = result.current
        if duty is None:
            logger.warning("Morning handoff: no duty curator")
            return
        text = format_morning_message(session, duty=duty, handoff=result)
        record_notification(
            session,
            kind="digest_morning",
            target_key=f"duty:{duty.id}:{schedule_service.school_today(session)}",
            details=f"reassigned={result.reassigned}",
        )
        session.commit()
    await _send_user(duty, text, reply_markup=open_tasks_keyboard())


async def job_evening_digest() -> None:
    if _session_factory is None:
        return
    with _session_factory() as session:
        duty = get_duty_curator(session)
        if duty is None:
            return
        text = format_evening_digest(session, duty=duty)
        record_notification(
            session,
            kind="digest_evening",
            target_key=f"duty:{duty.id}:{schedule_service.school_today(session)}",
        )
        session.commit()
    await _send_user(duty, text, reply_markup=open_tasks_keyboard())


async def job_urgency_scan() -> None:
    if _session_factory is None:
        return
    with _session_factory() as session:
        cfg = load_automation_config(session)
        urgent = list_urgent_tickets(session)
        duty = get_duty_curator(session)
        messages: list[tuple[Any, ...]] = []
        for ticket in urgent:
            key = urgency_target_key(ticket)
            if not can_send_urgency(session, target_key=key):
                continue
            text = format_urgency_message(ticket)
            record_notification(
                session, kind="urgency", target_key=key, details=text[:200]
            )
            targets: list[User] = []
            if duty is not None:
                targets.append(duty)
            if ticket.assignee is not None and (
                duty is None or ticket.assignee_id != duty.id
            ):
                targets.append(ticket.assignee)
            messages.append((targets, text, cfg.work_chat_id))
        session.commit()

    for targets, text, chat_id in messages:
        for user in targets:
            await _send_user(user, text)
        if chat_id:
            await _send_chat(chat_id, text)


async def job_reminder_ticket(ticket_id: int) -> None:
    if _session_factory is None:
        return
    with _session_factory() as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            reminders.cancel_ticket_reminder(ticket_id)
            return
        _ = ticket.assignee
        if ticket.status.value != "waiting_reply":
            reminders.cancel_ticket_reminder(ticket_id)
            session.commit()
            return
        text = reminders.format_ticket_reminder_text(ticket)
        assignee = ticket.assignee
        reminders.reschedule_after_fire_ticket(session, ticket)
        record_notification(
            session,
            kind="reminder",
            target_key=f"ticket:{ticket.id}",
            details="waiting_reply",
        )
        session.commit()
    if assignee is not None:
        await _send_user(assignee, text, reply_markup=open_tasks_keyboard())


async def job_weekly_stats_report() -> None:
    """Понедельник: краткий отчёт админам + кнопка подробной статистики."""
    if _session_factory is None:
        return
    with _session_factory() as session:
        text = format_weekly_summary(session)
        admins = list_admins(session)
        record_notification(
            session,
            kind="digest_weekly",
            target_key=f"weekly:{schedule_service.school_today(session)}",
            details=f"admins={len(admins)}",
        )
        session.commit()
    for admin in admins:
        await _send_user(admin, text, reply_markup=weekly_keyboard())


async def job_reminder_subtask(subtask_id: int) -> None:
    if _session_factory is None:
        return
    with _session_factory() as session:
        subtask = session.get(TicketSubtask, subtask_id)
        if subtask is None:
            reminders.cancel_subtask_reminder(subtask_id)
            return
        _ = subtask.ticket
        if subtask.ticket is not None:
            _ = subtask.ticket.assignee
        text = reminders.format_subtask_reminder_text(subtask)
        assignee = subtask.ticket.assignee if subtask.ticket else None
        reminders.reschedule_after_fire_subtask(session, subtask)
        record_notification(
            session,
            kind="reminder",
            target_key=f"subtask:{subtask.id}",
        )
        session.commit()
    if assignee is not None:
        await _send_user(assignee, text, reply_markup=open_tasks_keyboard())


def _register_callbacks(gateway) -> None:
    gateway.register_callback("reminder_ticket", job_reminder_ticket)
    gateway.register_callback("reminder_subtask", job_reminder_subtask)
    gateway.register_callback("duty_morning", job_morning_handoff)
    gateway.register_callback("duty_evening", job_evening_digest)
    gateway.register_callback("urgency_scan", job_urgency_scan)
    gateway.register_callback("weekly_stats", job_weekly_stats_report)


def setup_scheduler(
    *,
    database_url: str,
    session_factory: sessionmaker,
    bot: Bot | None = None,
    timezone: str = "Europe/Moscow",
) -> AsyncIOScheduler:
    """Создаёт AsyncIOScheduler с SQLAlchemy jobstore (переживает рестарт)."""
    global _scheduler, _session_factory, _bot
    _session_factory = session_factory
    _bot = bot

    # Отдельный файл SQLite для jobstore — не делить lock с заявками
    from bot.db.session import jobstore_url_from_database_url

    jobstore_url = jobstore_url_from_database_url(database_url)
    logger.info("APScheduler jobstore: %s", jobstore_url)
    jobstores = {
        "default": SQLAlchemyJobStore(
            url=jobstore_url,
            engine_options={
                "connect_args": {"check_same_thread": False, "timeout": 30},
            },
        )
    }
    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        timezone=timezone,
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    gateway = get_job_gateway()
    _register_callbacks(gateway)
    gateway.bind_scheduler(scheduler)

    with session_factory() as session:
        cfg = load_automation_config(session)
        tz_name = schedule_service.get_school_timezone_name(session)
        morning_h, morning_m = _parse_hhmm(cfg.morning_time)
        evening_h, evening_m = _parse_hhmm(cfg.evening_time)

    gateway.schedule_cron(
        JOB_DUTY_MORNING,
        hour=morning_h,
        minute=morning_m,
        timezone=tz_name,
        callback_name="duty_morning",
    )
    gateway.schedule_cron(
        JOB_DUTY_EVENING,
        hour=evening_h,
        minute=evening_m,
        timezone=tz_name,
        callback_name="duty_evening",
    )
    gateway.schedule_interval(
        JOB_URGENCY_SCAN,
        minutes=15,
        callback_name="urgency_scan",
    )
    # Еженедельный отчёт админу — понедельник 09:00 (часовой пояс школы)
    gateway.schedule_cron(
        JOB_WEEKLY_STATS,
        hour=9,
        minute=0,
        timezone=tz_name,
        day_of_week="mon",
        callback_name="weekly_stats",
    )

    _scheduler = scheduler
    return scheduler


def start_scheduler(scheduler: AsyncIOScheduler | None = None) -> None:
    sched = scheduler or _scheduler
    if sched is None:
        return
    if not sched.running:
        sched.start()
        logger.info("APScheduler started")


def get_bot() -> Bot | None:
    """Бот для фоновых уведомлений (статистика/предупреждения)."""
    return _bot


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
    _scheduler = None
