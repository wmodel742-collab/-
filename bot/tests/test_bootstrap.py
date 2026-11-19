"""Создание админа из env и init_db."""

from __future__ import annotations

from bot.db.models import SettingsRecord, User, UserRole
from bot.services.bootstrap import ensure_admin_from_env, init_db, seed_default_settings
from bot.services.users import get_active_user_by_telegram_id


def test_ensure_admin_from_env_creates(session):
    user = ensure_admin_from_env(session, 555, admin_name="Первый админ")
    session.commit()
    assert user is not None
    assert user.telegram_id == 555
    assert user.role == UserRole.ADMIN
    assert user.active is True
    assert user.name == "Первый админ"


def test_ensure_admin_from_env_upgrades_role(session):
    ensure_admin_from_env(session, 777, admin_name="Был куратор")
    session.commit()
    # понизим роль вручную и снова вызовем bootstrap
    user = session.query(User).filter_by(telegram_id=777).one()
    user.role = UserRole.CURATOR
    user.active = False
    session.commit()

    updated = ensure_admin_from_env(session, 777)
    session.commit()
    assert updated is not None
    assert updated.role == UserRole.ADMIN
    assert updated.active is True


def test_ensure_admin_skips_zero(session):
    assert ensure_admin_from_env(session, 0) is None


def test_init_db_and_settings(tmp_path):
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path.as_posix()}"
    factory = init_db(url)
    with factory() as session:
        seed_default_settings(session, "Europe/Moscow")
        ensure_admin_from_env(session, 42, admin_name="Admin")
        session.commit()
        tz = session.query(SettingsRecord).filter_by(key="timezone").one()
        assert tz.value == "Europe/Moscow"
        admin = get_active_user_by_telegram_id(session, 42)
        assert admin is not None
        assert admin.role == UserRole.ADMIN
