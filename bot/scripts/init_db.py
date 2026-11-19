"""Создание таблиц SQLite и первого администратора из .env.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe scripts\\init_db.py
    # или
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.services.bootstrap import ensure_admin_from_env, init_db, seed_default_settings


def main() -> None:
    settings = load_settings()
    session_factory = init_db(settings.database_url)
    with session_factory() as session:
        seed_default_settings(session, settings.timezone)
        admin = ensure_admin_from_env(session, settings.admin_telegram_id)
        session.commit()
    print("База данных готова.")
    print(f"DATABASE_URL={settings.database_url}")
    print(f"TIMEZONE={settings.timezone}")
    if admin is None:
        print("Админ не создан: задайте ADMIN_TELEGRAM_ID > 0 в .env")
    else:
        print(f"Админ: telegram_id={admin.telegram_id}, active={admin.active}")


if __name__ == "__main__":
    main()
