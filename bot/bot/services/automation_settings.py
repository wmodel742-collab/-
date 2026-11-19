"""Настройки автоматизации (интервалы, чаты, антиспам) — редактируемые админом."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from bot.services.schedule import get_setting, set_setting

# Ключи SettingsRecord
SETTING_REMINDER_WAITING_FIRST = "reminder_waiting_first_hours"
SETTING_REMINDER_WAITING_REPEAT = "reminder_waiting_repeat_hours"
SETTING_REMINDER_SEARCHING = "reminder_searching_hours"
SETTING_REMINDER_FOUND = "reminder_found_hours"
SETTING_URGENCY_TRANSFER_HOURS = "urgency_transfer_hours"
SETTING_URGENCY_REPLACEMENT_HOURS = "urgency_replacement_hours"
SETTING_URGENCY_ANTISPAM_MINUTES = "urgency_antispam_minutes"
SETTING_WORK_CHAT_ID = "work_chat_id"
SETTING_NOTIFY_OFF_HOURS = "notify_urgency_off_hours"
SETTING_MORNING_TIME = "duty_morning_time"
SETTING_EVENING_TIME = "duty_evening_time"

DEFAULTS: dict[str, str] = {
    SETTING_REMINDER_WAITING_FIRST: "2",
    SETTING_REMINDER_WAITING_REPEAT: "3",
    SETTING_REMINDER_SEARCHING: "4",
    SETTING_REMINDER_FOUND: "2",
    SETTING_URGENCY_TRANSFER_HOURS: "3",
    SETTING_URGENCY_REPLACEMENT_HOURS: "24",
    SETTING_URGENCY_ANTISPAM_MINUTES: "60",
    SETTING_WORK_CHAT_ID: "",
    SETTING_NOTIFY_OFF_HOURS: "false",
    SETTING_MORNING_TIME: "08:00",
    SETTING_EVENING_TIME: "20:45",
}

SETTING_LABELS_RU: dict[str, str] = {
    SETTING_REMINDER_WAITING_FIRST: "Ждём ответа: первое напоминание (раб.часы)",
    SETTING_REMINDER_WAITING_REPEAT: "Ждём ответа: повтор каждые (раб.часы)",
    SETTING_REMINDER_SEARCHING: "Замена «ищем»: интервал (раб.часы)",
    SETTING_REMINDER_FOUND: "Замена «найден»: интервал (раб.часы)",
    SETTING_URGENCY_TRANSFER_HOURS: "Срочность переноса: часов до занятия",
    SETTING_URGENCY_REPLACEMENT_HOURS: "Срочность замены: часов до ближайшего",
    SETTING_URGENCY_ANTISPAM_MINUTES: "Антиспам срочных уведомлений (минуты)",
    SETTING_WORK_CHAT_ID: "ID рабочего чата (срочные)",
    SETTING_NOTIFY_OFF_HOURS: "Срочные вне рабочих часов (true/false)",
    SETTING_MORNING_TIME: "Смена дежурства / доброе утро",
    SETTING_EVENING_TIME: "Вечерняя сводка незавершённых",
}

EDITABLE_KEYS: tuple[str, ...] = tuple(DEFAULTS.keys())


@dataclass(frozen=True, slots=True)
class AutomationConfig:
    waiting_first_hours: float
    waiting_repeat_hours: float
    searching_hours: float
    found_hours: float
    urgency_transfer_hours: float
    urgency_replacement_hours: float
    urgency_antispam_minutes: int
    work_chat_id: int | None
    notify_off_hours: bool
    morning_time: str
    evening_time: str


def seed_automation_settings(session: Session) -> None:
    for key, value in DEFAULTS.items():
        existing = get_setting(session, key)
        if existing is None:
            set_setting(session, key, value)


def _as_float(raw: str | None, default: str) -> float:
    text = (raw if raw is not None else default).strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return float(default)


def _as_int(raw: str | None, default: str) -> int:
    text = (raw if raw is not None else default).strip()
    try:
        return int(float(text))
    except ValueError:
        return int(default)


def _as_bool(raw: str | None, default: str = "false") -> bool:
    text = (raw if raw is not None else default).strip().casefold()
    return text in {"1", "true", "yes", "on", "да"}


def _as_chat_id(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def load_automation_config(session: Session) -> AutomationConfig:
    seed_automation_settings(session)
    return AutomationConfig(
        waiting_first_hours=_as_float(
            get_setting(session, SETTING_REMINDER_WAITING_FIRST),
            DEFAULTS[SETTING_REMINDER_WAITING_FIRST],
        ),
        waiting_repeat_hours=_as_float(
            get_setting(session, SETTING_REMINDER_WAITING_REPEAT),
            DEFAULTS[SETTING_REMINDER_WAITING_REPEAT],
        ),
        searching_hours=_as_float(
            get_setting(session, SETTING_REMINDER_SEARCHING),
            DEFAULTS[SETTING_REMINDER_SEARCHING],
        ),
        found_hours=_as_float(
            get_setting(session, SETTING_REMINDER_FOUND),
            DEFAULTS[SETTING_REMINDER_FOUND],
        ),
        urgency_transfer_hours=_as_float(
            get_setting(session, SETTING_URGENCY_TRANSFER_HOURS),
            DEFAULTS[SETTING_URGENCY_TRANSFER_HOURS],
        ),
        urgency_replacement_hours=_as_float(
            get_setting(session, SETTING_URGENCY_REPLACEMENT_HOURS),
            DEFAULTS[SETTING_URGENCY_REPLACEMENT_HOURS],
        ),
        urgency_antispam_minutes=_as_int(
            get_setting(session, SETTING_URGENCY_ANTISPAM_MINUTES),
            DEFAULTS[SETTING_URGENCY_ANTISPAM_MINUTES],
        ),
        work_chat_id=_as_chat_id(get_setting(session, SETTING_WORK_CHAT_ID)),
        notify_off_hours=_as_bool(
            get_setting(session, SETTING_NOTIFY_OFF_HOURS),
            DEFAULTS[SETTING_NOTIFY_OFF_HOURS],
        ),
        morning_time=(
            get_setting(session, SETTING_MORNING_TIME)
            or DEFAULTS[SETTING_MORNING_TIME]
        ).strip(),
        evening_time=(
            get_setting(session, SETTING_EVENING_TIME)
            or DEFAULTS[SETTING_EVENING_TIME]
        ).strip(),
    )


def format_settings_summary(session: Session) -> str:
    cfg = load_automation_config(session)
    chat = str(cfg.work_chat_id) if cfg.work_chat_id is not None else "не задан"
    lines = [
        "⚙️ Настройки автоматизации",
        "",
        f"• Ждём ответа: первое через {cfg.waiting_first_hours:g} ч, "
        f"далее каждые {cfg.waiting_repeat_hours:g} ч",
        f"• Замена «ищем»: каждые {cfg.searching_hours:g} ч",
        f"• Замена «найден»: каждые {cfg.found_hours:g} ч",
        f"• Срочность переноса: < {cfg.urgency_transfer_hours:g} ч",
        f"• Срочность замены: < {cfg.urgency_replacement_hours:g} ч",
        f"• Антиспам срочных: {cfg.urgency_antispam_minutes} мин",
        f"• Рабочий чат: {chat}",
        f"• Срочные вне смены: {'да' if cfg.notify_off_hours else 'нет'}",
        f"• Утро / смена: {cfg.morning_time}",
        f"• Вечерняя сводка: {cfg.evening_time}",
        "",
        "Выберите параметр для изменения.",
    ]
    return "\n".join(lines)
