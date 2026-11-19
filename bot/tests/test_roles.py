"""Роли, доступ и меню."""

from __future__ import annotations

from bot.db.models import UserRole
from bot.keyboards.menus import main_menu_keyboard
from bot.middlewares.auth import user_has_role
from bot.services.users import (
    create_employee,
    deactivate_employee,
    get_active_user_by_telegram_id,
    list_employees,
)


def _button_texts(user) -> set[str]:
    kb = main_menu_keyboard(user)
    texts: set[str] = set()
    for row in kb.keyboard:
        for btn in row:
            texts.add(btn.text)
    return texts


def test_unauthorized_user_has_no_active_record(session):
    assert get_active_user_by_telegram_id(session, 999999) is None


def test_inactive_user_not_active(session, curator_user):
    deactivate_employee(session, telegram_id=curator_user.telegram_id, actor_telegram_id=1001)
    session.commit()
    assert get_active_user_by_telegram_id(session, curator_user.telegram_id) is None


def test_admin_has_full_menu(admin_user):
    texts = _button_texts(admin_user)
    assert "Добавить перенос" in texts
    assert "Создать заявку" in texts
    assert "Управление сотрудниками" in texts
    assert "Статистика" in texts
    assert "Настройки" in texts
    assert "График дежурного" in texts
    assert "Управление графиком" not in texts


def test_curator_menu(curator_user):
    texts = _button_texts(curator_user)
    assert "Добавить перенос" in texts
    assert "Поиск заявки" in texts
    assert "Управление сотрудниками" not in texts
    assert "Создать заявку" not in texts


def test_senior_menu(senior_user):
    texts = _button_texts(senior_user)
    assert "Создать заявку" in texts
    assert "Мои заявки" in texts
    assert "Найти заявку" in texts
    assert "Добавить перенос" not in texts
    assert "Управление сотрудниками" not in texts


def test_user_has_role_admin_bypass(admin_user, curator_user):
    assert user_has_role(admin_user, UserRole.CURATOR) is True
    assert user_has_role(curator_user, UserRole.CURATOR) is True
    assert user_has_role(curator_user, UserRole.ADMIN) is False
    assert user_has_role(curator_user, UserRole.SENIOR_TEACHER) is False


def test_create_and_list_employees(session, admin_user):
    create_employee(
        session,
        telegram_id=4004,
        name="Новый куратор",
        role=UserRole.CURATOR,
        actor_telegram_id=admin_user.telegram_id,
    )
    session.commit()
    employees = list_employees(session)
    ids = {u.telegram_id for u in employees}
    assert 4004 in ids
