"""Инициализация БД и первого администратора."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from bot.db.base import Base
from bot.db.models import SettingsRecord, User, UserRole
from bot.db.session import create_engine, create_session_factory
from bot.services.automation_settings import seed_automation_settings
from bot.services.schedule import ensure_default_cycle, seed_schedule_settings
from bot.services.users import write_audit


def init_db(database_url: str) -> sessionmaker:
    """Создаёт таблицы и возвращает фабрику сессий."""
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    return create_session_factory(engine)


def seed_default_settings(session: Session, timezone: str) -> None:
    """Записывает базовые настройки школы, если их ещё нет."""
    defaults = {
        "timezone": timezone,
        "school_name": "АвоПеренос",
    }
    for key, value in defaults.items():
        existing = session.query(SettingsRecord).filter_by(key=key).one_or_none()
        if existing is None:
            session.add(SettingsRecord(key=key, value=value))
        elif key == "timezone" and existing.value != timezone:
            existing.value = timezone
    session.flush()
    seed_schedule_settings(session, timezone=timezone)
    seed_automation_settings(session)
    ensure_default_cycle(session)


def ensure_admin_from_env(
    session: Session,
    admin_telegram_id: int,
    *,
    admin_name: str = "Администратор",
) -> User | None:
    """Создаёт или обновляет первого админа по ADMIN_TELEGRAM_ID.

    Если ID равен 0 или отрицательный — пропускает (удобно для тестов без env).
    """
    if admin_telegram_id <= 0:
        return None

    user = session.query(User).filter_by(telegram_id=admin_telegram_id).one_or_none()
    if user is None:
        user = User(
            telegram_id=admin_telegram_id,
            name=admin_name,
            role=UserRole.ADMIN,
            active=True,
        )
        session.add(user)
        session.flush()
        write_audit(
            session,
            actor_telegram_id=admin_telegram_id,
            action="bootstrap_admin_created",
            details=f"telegram_id={admin_telegram_id}",
        )
        return user

    changed = False
    if user.role != UserRole.ADMIN:
        user.role = UserRole.ADMIN
        changed = True
    if not user.active:
        user.active = True
        changed = True
    if changed:
        session.flush()
        write_audit(
            session,
            actor_telegram_id=admin_telegram_id,
            action="bootstrap_admin_updated",
            details=f"telegram_id={admin_telegram_id}",
        )
    return user
