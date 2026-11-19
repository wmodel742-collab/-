"""Регистрация роутеров."""

from aiogram import Router

from bot.handlers import (
    employees,
    mass,
    menu,
    replacement,
    schedule,
    settings,
    start,
    stats,
    tickets,
)


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(employees.router)
    # рабочие разделы до menu — иначе заглушки перехватят те же пункты
    root.include_router(tickets.router)
    root.include_router(mass.router)
    root.include_router(replacement.router)
    root.include_router(schedule.router)
    root.include_router(settings.router)
    root.include_router(stats.router)
    root.include_router(menu.router)
    return root
