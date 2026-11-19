"""Дежурный куратор по графику 2/2 (этап 5)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from bot.db.models import User, UserRole
from bot.services import schedule as schedule_service


def get_duty_curator(session: Session, on_date: date | None = None) -> User | None:
    """Вернуть дежурного куратора на дату (график 2/2 + разовые подмены).

    Дата без аргумента — «сегодня» школы в TIMEZONE из Settings, не системные часы хоста.
    Если цикл ещё не создан (нет пары Ксения/Юлия) — fallback: первый активный куратор.
    """
    day = on_date if on_date is not None else schedule_service.school_today(session)
    info = schedule_service.get_duty_info(session, day)
    if info is not None:
        return info.curator

    return (
        session.query(User)
        .filter(User.role == UserRole.CURATOR, User.active.is_(True))
        .order_by(User.id.asc())
        .first()
    )


def resolve_assignee_for_new_ticket(session: Session, creator: User) -> User | None:
    """Назначить ответственного при создании заявки.

    - создатель-куратор → себе;
    - администратор / старший / прочие → дежурный куратор на «сегодня» школы
      (чтобы заявка сразу была у дежурного, а не «висела» на админе).
    """
    if creator.role == UserRole.CURATOR:
        return creator
    return get_duty_curator(session, None)
