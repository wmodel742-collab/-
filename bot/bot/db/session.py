"""Engine и фабрика сессий SQLAlchemy."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def jobstore_url_from_database_url(database_url: str) -> str:
    """Отдельный SQLite-файл для APScheduler — без lock с основной БД заявок.

    sqlite:///./avoperenos.db → sqlite:///./avoperenos.jobs.db
    """
    if not database_url.startswith("sqlite"):
        return database_url
    if ":memory:" in database_url:
        return "sqlite:///:memory:"
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return database_url
    raw = database_url[len(prefix) :]
    path = Path(raw)
    jobs = path.with_name(f"{path.stem}.jobs{path.suffix or '.db'}")
    return f"sqlite:///{jobs.as_posix()}"


def create_engine(database_url: str) -> Engine:
    """Создаёт engine с настройками для SQLite (WAL + busy_timeout)."""
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        # timeout — секунды ожидания при locked (sqlite3)
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
    engine = sa_create_engine(database_url, future=True, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def get_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
