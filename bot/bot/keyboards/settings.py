"""Клавиатуры админ-настроек автоматизации."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.automation_settings import EDITABLE_KEYS, SETTING_LABELS_RU


class SettingsCB:
    PREFIX = "set:"
    EDIT = "set:edit:"
    BACK = "set:back"


def settings_root_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in EDITABLE_KEYS:
        label = SETTING_LABELS_RU.get(key, key)
        # укоротить для кнопки
        short = label if len(label) <= 60 else label[:57] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=short,
                    callback_data=f"{SettingsCB.EDIT}{key}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="◀️ В меню", callback_data="menu:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=SettingsCB.BACK,
                )
            ]
        ]
    )
