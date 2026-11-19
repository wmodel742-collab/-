"""Общие фикстуры pytest."""

from __future__ import annotations

import os

import pytest
from sqlalchemy.orm import Session, sessionmaker

# Токен-заглушка только для тестов/импорта настроек — не для реального polling
os.environ.setdefault("BOT_TOKEN", "000000000:TEST_TOKEN_FOR_PYTEST_ONLY")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1001")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TIMEZONE", "Europe/Moscow")

from bot.db.base import Base
from bot.db.models import User, UserRole
from bot.db.session import create_engine, create_session_factory
from bot.services.bootstrap import ensure_admin_from_env, seed_default_settings
from bot.services.users import create_employee


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def session(session_factory: sessionmaker[Session]):
    with session_factory() as s:
        seed_default_settings(s, "Europe/Moscow")
        s.commit()
        yield s


@pytest.fixture()
def admin_user(session: Session) -> User:
    user = ensure_admin_from_env(session, 1001, admin_name="Rais Admin")
    session.commit()
    assert user is not None
    return user


@pytest.fixture()
def curator_user(session: Session) -> User:
    user = create_employee(
        session,
        telegram_id=2002,
        name="Куратор Тест",
        role=UserRole.CURATOR,
        actor_telegram_id=1001,
    )
    session.commit()
    return user


@pytest.fixture()
def senior_user(session: Session) -> User:
    user = create_employee(
        session,
        telegram_id=3003,
        name="Старший Тест",
        role=UserRole.SENIOR_TEACHER,
        actor_telegram_id=1001,
    )
    session.commit()
    return user
