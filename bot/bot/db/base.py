"""Базовый declarative для SQLAlchemy 2."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общая база моделей."""
