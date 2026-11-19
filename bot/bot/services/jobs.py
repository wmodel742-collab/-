"""Устойчивый реестр заданий: APScheduler + in-memory для тестов.

Стабильные job_id + replace_existing исключают дубли при рестарте и повторном
планировании. Jobstore — отдельный SQLite-файл (см. jobstore_url_from_database_url).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger("avoperenos.jobs")


def _with_jobstore_retry(action: str, fn: Callable[[], Any], *, attempts: int = 5) -> Any:
    """Повтор при sqlite locked / временных сбоях jobstore."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — jobstore не должен ронять заявки
            last = exc
            msg = str(exc).lower()
            if "locked" not in msg and "database is locked" not in msg:
                raise
            delay = 0.05 * (2**i)
            logger.warning(
                "jobstore %s locked (try %s/%s), sleep %.2fs: %s",
                action,
                i + 1,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last is not None
    raise last

# Префиксы стабильных id
JOB_TICKET_REMINDER = "reminder:ticket:"
JOB_SUBTASK_REMINDER = "reminder:subtask:"
JOB_DUTY_MORNING = "duty:morning"
JOB_DUTY_EVENING = "duty:evening"
JOB_URGENCY_SCAN = "urgency:scan"
JOB_WEEKLY_STATS = "stats:weekly"


@dataclass
class JobRecord:
    job_id: str
    run_at: datetime | None
    callback_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    trigger: str = "date"  # date | cron


class JobGateway:
    """Абстракция планировщика: память (тесты) или APScheduler (прод)."""

    def __init__(self) -> None:
        self._memory: dict[str, JobRecord] = {}
        self._scheduler = None  # AsyncIOScheduler | BackgroundScheduler
        self._callbacks: dict[str, Callable[..., Any]] = {}

    @property
    def using_apscheduler(self) -> bool:
        return self._scheduler is not None

    def register_callback(self, name: str, fn: Callable[..., Any]) -> None:
        self._callbacks[name] = fn

    def bind_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def schedule_date(
        self,
        job_id: str,
        run_at: datetime,
        callback_name: str,
        **kwargs: Any,
    ) -> None:
        if run_at.tzinfo is None:
            raise ValueError("run_at must be timezone-aware")
        record = JobRecord(
            job_id=job_id,
            run_at=run_at,
            callback_name=callback_name,
            kwargs=dict(kwargs),
            trigger="date",
        )
        self._memory[job_id] = record
        if self._scheduler is not None:
            fn = self._resolve(callback_name)

            def _add() -> None:
                self._scheduler.add_job(
                    fn,
                    trigger="date",
                    run_date=run_at,
                    id=job_id,
                    replace_existing=True,
                    kwargs=kwargs,
                    misfire_grace_time=3600,
                )

            _with_jobstore_retry(f"add:{job_id}", _add)

    def schedule_cron(
        self,
        job_id: str,
        *,
        hour: int,
        minute: int,
        timezone: str | ZoneInfo,
        callback_name: str,
        day_of_week: str | int | None = None,
        **kwargs: Any,
    ) -> None:
        cron_meta = {"day_of_week": day_of_week} if day_of_week is not None else {}
        record = JobRecord(
            job_id=job_id,
            run_at=None,
            callback_name=callback_name,
            kwargs={**dict(kwargs), **cron_meta},
            trigger="cron",
        )
        self._memory[job_id] = record
        if self._scheduler is not None:
            from apscheduler.triggers.cron import CronTrigger

            fn = self._resolve(callback_name)
            trigger_kwargs: dict[str, Any] = {
                "hour": hour,
                "minute": minute,
                "timezone": timezone,
            }
            if day_of_week is not None:
                trigger_kwargs["day_of_week"] = day_of_week

            def _add() -> None:
                self._scheduler.add_job(
                    fn,
                    trigger=CronTrigger(**trigger_kwargs),
                    id=job_id,
                    replace_existing=True,
                    kwargs=kwargs,
                    misfire_grace_time=3600,
                )

            _with_jobstore_retry(f"cron:{job_id}", _add)

    def schedule_interval(
        self,
        job_id: str,
        *,
        minutes: int,
        callback_name: str,
        **kwargs: Any,
    ) -> None:
        record = JobRecord(
            job_id=job_id,
            run_at=None,
            callback_name=callback_name,
            kwargs=dict(kwargs),
            trigger="interval",
        )
        self._memory[job_id] = record
        if self._scheduler is not None:
            fn = self._resolve(callback_name)

            def _add() -> None:
                self._scheduler.add_job(
                    fn,
                    trigger="interval",
                    minutes=minutes,
                    id=job_id,
                    replace_existing=True,
                    kwargs=kwargs,
                    misfire_grace_time=600,
                )

            _with_jobstore_retry(f"interval:{job_id}", _add)

    def remove_job(self, job_id: str) -> bool:
        existed = job_id in self._memory
        self._memory.pop(job_id, None)
        if self._scheduler is not None:
            try:
                self._scheduler.remove_job(job_id)
                return True
            except Exception:
                return existed
        return existed

    def has_job(self, job_id: str) -> bool:
        if job_id in self._memory:
            return True
        if self._scheduler is not None:
            return self._scheduler.get_job(job_id) is not None
        return False

    def list_job_ids(self, prefix: str | None = None) -> list[str]:
        ids = set(self._memory.keys())
        if self._scheduler is not None:
            for job in self._scheduler.get_jobs():
                ids.add(job.id)
        result = sorted(ids)
        if prefix:
            result = [j for j in result if j.startswith(prefix)]
        return result

    def get_record(self, job_id: str) -> JobRecord | None:
        return self._memory.get(job_id)

    def clear(self) -> None:
        for job_id in list(self._memory.keys()):
            self.remove_job(job_id)
        self._memory.clear()

    def _resolve(self, callback_name: str) -> Callable[..., Any]:
        if callback_name not in self._callbacks:
            raise KeyError(f"Callback not registered: {callback_name}")
        return self._callbacks[callback_name]


# Глобальный шлюз приложения (тесты подменяют / сбрасывают)
_gateway = JobGateway()


def get_job_gateway() -> JobGateway:
    return _gateway


def reset_job_gateway() -> JobGateway:
    global _gateway
    _gateway = JobGateway()
    return _gateway


def ticket_reminder_job_id(ticket_id: int) -> str:
    return f"{JOB_TICKET_REMINDER}{ticket_id}"


def subtask_reminder_job_id(subtask_id: int) -> str:
    return f"{JOB_SUBTASK_REMINDER}{subtask_id}"
