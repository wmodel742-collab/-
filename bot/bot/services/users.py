"""CRUD сотрудников и проверка доступа."""

from __future__ import annotations

from sqlalchemy.orm import Session

from bot.db.models import AuditLog, ROLE_LABELS_RU, User, UserRole


def write_audit(
    session: Session,
    *,
    actor_telegram_id: int | None,
    action: str,
    details: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_telegram_id=actor_telegram_id,
        action=action,
        details=details,
    )
    session.add(entry)
    session.flush()
    return entry


def get_active_user_by_telegram_id(session: Session, telegram_id: int) -> User | None:
    return (
        session.query(User)
        .filter(User.telegram_id == telegram_id, User.active.is_(True))
        .one_or_none()
    )


def get_user_by_telegram_id(session: Session, telegram_id: int) -> User | None:
    return session.query(User).filter(User.telegram_id == telegram_id).one_or_none()


def list_employees(session: Session, *, include_inactive: bool = True) -> list[User]:
    query = session.query(User).order_by(User.name.asc())
    if not include_inactive:
        query = query.filter(User.active.is_(True))
    return list(query.all())


def create_employee(
    session: Session,
    *,
    telegram_id: int,
    name: str,
    role: UserRole,
    actor_telegram_id: int | None = None,
) -> User:
    if telegram_id <= 0:
        raise ValueError("Telegram ID должен быть положительным числом.")
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Имя сотрудника не может быть пустым.")

    existing = get_user_by_telegram_id(session, telegram_id)
    if existing is not None:
        if existing.active:
            raise ValueError(
                f"Сотрудник с Telegram ID {telegram_id} уже есть: {existing.name}."
            )
        existing.name = clean_name
        existing.role = role
        existing.active = True
        session.flush()
        write_audit(
            session,
            actor_telegram_id=actor_telegram_id,
            action="employee_reactivated",
            details=f"id={existing.id}; telegram_id={telegram_id}; role={role.value}",
        )
        return existing

    user = User(
        telegram_id=telegram_id,
        name=clean_name,
        role=role,
        active=True,
    )
    session.add(user)
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor_telegram_id,
        action="employee_created",
        details=f"id={user.id}; telegram_id={telegram_id}; role={role.value}",
    )
    return user


def deactivate_employee(
    session: Session,
    *,
    telegram_id: int,
    actor_telegram_id: int | None = None,
) -> User:
    user = get_user_by_telegram_id(session, telegram_id)
    if user is None:
        raise ValueError(f"Сотрудник с Telegram ID {telegram_id} не найден.")
    if not user.active:
        raise ValueError(f"Сотрудник {user.name} уже деактивирован.")
    user.active = False
    session.flush()
    write_audit(
        session,
        actor_telegram_id=actor_telegram_id,
        action="employee_deactivated",
        details=f"id={user.id}; telegram_id={telegram_id}",
    )
    return user


def format_employee_line(user: User) -> str:
    status = "активен" if user.active else "неактивен"
    role = ROLE_LABELS_RU.get(user.role, user.role.value)
    return f"• {user.name} — {role}, ID {user.telegram_id} ({status})"


def parse_role(raw: str) -> UserRole:
    normalized = raw.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "admin": UserRole.ADMIN,
        "админ": UserRole.ADMIN,
        "администратор": UserRole.ADMIN,
        "senior": UserRole.SENIOR_TEACHER,
        "senior_teacher": UserRole.SENIOR_TEACHER,
        "старший": UserRole.SENIOR_TEACHER,
        "старший_преподаватель": UserRole.SENIOR_TEACHER,
        "curator": UserRole.CURATOR,
        "куратор": UserRole.CURATOR,
    }
    role = aliases.get(normalized)
    if role is None:
        raise ValueError(
            "Неизвестная роль. Допустимо: admin / senior_teacher / curator "
            "(или админ / старший / куратор)."
        )
    return role
