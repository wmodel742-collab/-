"""Главное меню по ролям и клавиатуры управления сотрудниками."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.db.models import User, UserRole


class CallbackData:
    """Префиксы callback_data для inline-кнопок."""

    MENU = "menu:"
    EMP = "emp:"
    ROLE = "role:"
    STUB = "stub:"


# Пункты следующих этапов — отвечают «раздел в разработке»
# (этапы 2–7: переносы / массовый / замены / график / настройки / статистика — не заглушки)
STUB_SECTIONS: dict[str, str] = {}


def main_menu_keyboard(user: User) -> ReplyKeyboardMarkup:
    """Reply-клавиатура главного меню в зависимости от роли."""
    rows: list[list[KeyboardButton]] = []

    if user.role in (UserRole.CURATOR, UserRole.ADMIN):
        rows.append(
            [
                KeyboardButton(text="Добавить перенос"),
                KeyboardButton(text="Массовый перенос"),
            ]
        )
        rows.append(
            [
                KeyboardButton(text="Подбор замены"),
                KeyboardButton(text="Активные заявки"),
            ]
        )
        rows.append(
            [
                KeyboardButton(text="На контроле"),
                KeyboardButton(text="График кураторов"),
            ]
        )
        rows.append([KeyboardButton(text="Поиск заявки")])

    if user.role in (UserRole.SENIOR_TEACHER, UserRole.ADMIN):
        rows.append(
            [
                KeyboardButton(text="Создать заявку"),
                KeyboardButton(text="Мои заявки"),
            ]
        )
        # Старший: подбор замены из своего меню (у админа уже есть в блоке куратора)
        if user.role == UserRole.SENIOR_TEACHER:
            rows.append([KeyboardButton(text="Подбор замены")])
        rows.append([KeyboardButton(text="Найти заявку")])

    if user.role == UserRole.ADMIN:
        rows.append(
            [
                KeyboardButton(text="Статистика"),
                KeyboardButton(text="График дежурного"),
            ]
        )
        rows.append(
            [
                KeyboardButton(text="Управление сотрудниками"),
                KeyboardButton(text="Настройки"),
            ]
        )

    # Убрать возможные дубликаты при полном меню админа (если тексты совпали)
    seen: set[str] = set()
    unique_rows: list[list[KeyboardButton]] = []
    for row in rows:
        new_row: list[KeyboardButton] = []
        for btn in row:
            if btn.text not in seen:
                seen.add(btn.text)
                new_row.append(btn)
        if new_row:
            unique_rows.append(new_row)

    return ReplyKeyboardMarkup(
        keyboard=unique_rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите пункт меню",
    )


def employee_manage_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить сотрудника",
                    callback_data=f"{CallbackData.EMP}add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Список сотрудников",
                    callback_data=f"{CallbackData.EMP}list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Деактивировать доступ",
                    callback_data=f"{CallbackData.EMP}deactivate",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ В меню",
                    callback_data=f"{CallbackData.MENU}home",
                )
            ],
        ]
    )


def employee_roles_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Администратор",
                    callback_data=f"{CallbackData.ROLE}admin",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Старший преподаватель",
                    callback_data=f"{CallbackData.ROLE}senior_teacher",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Куратор",
                    callback_data=f"{CallbackData.ROLE}curator",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"{CallbackData.EMP}cancel",
                )
            ],
        ]
    )
