"""Модели сотрудников, заявок, настроек и журнала аудита."""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    """Роли доступа к боту."""

    ADMIN = "admin"
    SENIOR_TEACHER = "senior_teacher"
    CURATOR = "curator"


ROLE_LABELS_RU: dict[UserRole, str] = {
    UserRole.ADMIN: "Администратор",
    UserRole.SENIOR_TEACHER: "Старший преподаватель",
    UserRole.CURATOR: "Куратор",
}


class TicketType(str, enum.Enum):
    ONE_TIME = "one_time"
    PERMANENT = "permanent"
    MASS = "mass"
    DISMISSAL = "dismissal"
    TEMPORARY_ABSENCE = "temporary_absence"


TICKET_TYPE_LABELS_RU: dict[TicketType, str] = {
    TicketType.ONE_TIME: "Разовый перенос",
    TicketType.PERMANENT: "Постоянный перенос",
    TicketType.MASS: "Массовый перенос",
    TicketType.DISMISSAL: "Увольнение / подбор замены",
    TicketType.TEMPORARY_ABSENCE: "Временное отсутствие / замена",
}

REPLACEMENT_TICKET_TYPES: frozenset[TicketType] = frozenset(
    {TicketType.DISMISSAL, TicketType.TEMPORARY_ABSENCE}
)


class TicketStatus(str, enum.Enum):
    NOT_PROCESSED = "not_processed"
    WAITING_REPLY = "waiting_reply"
    DONE = "done"
    REJECTED = "rejected"


TICKET_STATUS_LABELS_RU: dict[TicketStatus, str] = {
    TicketStatus.NOT_PROCESSED: "Ещё не написали",
    TicketStatus.WAITING_REPLY: "Ждём ответа",
    TicketStatus.DONE: "Выполнено",
    TicketStatus.REJECTED: "Отказ",
}

# Подзадача «закрыта» — не блокирует закрытие общей массовой заявки
CLOSED_SUBTASK_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.DONE, TicketStatus.REJECTED}
)


class TicketInitiator(str, enum.Enum):
    TEACHER = "teacher"
    STUDENT_PARENT = "student_parent"
    SCHOOL = "school"


TICKET_INITIATOR_LABELS_RU: dict[TicketInitiator, str] = {
    TicketInitiator.TEACHER: "Преподаватель",
    TicketInitiator.STUDENT_PARENT: "Ученик / родитель",
    TicketInitiator.SCHOOL: "Школа",
}


class MassSubtaskAction(str, enum.Enum):
    """Необходимое действие по ученику в массовой заявке."""

    PROPOSE_TRANSFER = "propose_transfer"
    AGREE_CANCEL = "agree_cancel"
    TEMP_REPLACEMENT = "temp_replacement"
    CLARIFY_PARENT = "clarify_parent"


MASS_SUBTASK_ACTION_LABELS_RU: dict[MassSubtaskAction, str] = {
    MassSubtaskAction.PROPOSE_TRANSFER: "Предложить перенос",
    MassSubtaskAction.AGREE_CANCEL: "Согласовать отмену",
    MassSubtaskAction.TEMP_REPLACEMENT: "Временная замена",
    MassSubtaskAction.CLARIFY_PARENT: "Уточнить у родителя",
}


class ReplacementStage(str, enum.Enum):
    """Этапы подбора замены по ученику."""

    SEARCHING = "searching"
    FOUND = "found"
    OFFERED = "offered"
    AGREED = "agreed"
    CLOSED_NO_CONTINUE = "closed_no_continue"


REPLACEMENT_STAGE_LABELS_RU: dict[ReplacementStage, str] = {
    ReplacementStage.SEARCHING: "Ищем преподавателя",
    ReplacementStage.FOUND: "Найден кандидат",
    ReplacementStage.OFFERED: "Предложили родителю",
    ReplacementStage.AGREED: "Согласовано / выполнено",
    ReplacementStage.CLOSED_NO_CONTINUE: "Ученик не продолжает",
}

CLOSED_REPLACEMENT_STAGES: frozenset[ReplacementStage] = frozenset(
    {ReplacementStage.AGREED, ReplacementStage.CLOSED_NO_CONTINUE}
)

REPLACEMENT_STAGE_TO_STATUS: dict[ReplacementStage, TicketStatus] = {
    ReplacementStage.SEARCHING: TicketStatus.NOT_PROCESSED,
    ReplacementStage.FOUND: TicketStatus.NOT_PROCESSED,
    ReplacementStage.OFFERED: TicketStatus.WAITING_REPLY,
    ReplacementStage.AGREED: TicketStatus.DONE,
    ReplacementStage.CLOSED_NO_CONTINUE: TicketStatus.REJECTED,
}


class ProposalOutcome(str, enum.Enum):
    """Исход предложения кандидата-замены."""

    ACTIVE = "active"
    ACCEPTED = "accepted"
    REJECTED_SEARCH_ANOTHER = "rejected_search_another"
    SUPERSEDED = "superseded"


PROPOSAL_OUTCOME_LABELS_RU: dict[ProposalOutcome, str] = {
    ProposalOutcome.ACTIVE: "Текущий кандидат",
    ProposalOutcome.ACCEPTED: "Принят",
    ProposalOutcome.REJECTED_SEARCH_ANOTHER: "Ищем другого",
    ProposalOutcome.SUPERSEDED: "Заменён другим",
}


WEEKDAY_LABELS_RU: dict[int, str] = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}


class User(Base):
    """Сотрудник школы с доступом к боту."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=False),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    created_tickets: Mapped[list[Ticket]] = relationship(
        "Ticket",
        back_populates="creator",
        foreign_keys="Ticket.creator_id",
    )
    assigned_tickets: Mapped[list[Ticket]] = relationship(
        "Ticket",
        back_populates="assignee",
        foreign_keys="Ticket.assignee_id",
    )


class SettingsRecord(Base):
    """Ключ-значение настроек школы (часовой пояс, чаты и т.д.)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ScheduleCycle(Base):
    """Основной цикл дежурств 2/2, действующий с effective_from.

    Относительно anchor_date: дни 0–1 — first_curator, 2–3 — second_curator,
    далее повтор. Новые циклы не сбрасывают прошлые даты; разовые подмены
    живут в ScheduleDayOverride и не меняют фазу цикла.
    """

    __tablename__ = "schedule_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_curator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    second_curator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    first_curator: Mapped[User] = relationship("User", foreign_keys=[first_curator_id])
    second_curator: Mapped[User] = relationship("User", foreign_keys=[second_curator_id])
    created_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )


class ScheduleDayOverride(Base):
    """Разовая подмена дежурного на конкретный день (не ломает цикл 2/2)."""

    __tablename__ = "schedule_day_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    curator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    curator: Mapped[User] = relationship("User", foreign_keys=[curator_id])
    created_by: Mapped[User | None] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )


class AuditLog(Base):
    """Журнал значимых действий."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class NotificationLog(Base):
    """Журнал уведомлений (антиспам срочных, аудит дайджестов)."""

    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class Ticket(Base):
    """Заявка на разовый или постоянный перенос."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    ticket_type: Mapped[TicketType] = mapped_column(
        Enum(TicketType, name="ticket_type", native_enum=False),
        nullable=False,
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status", native_enum=False),
        nullable=False,
        default=TicketStatus.NOT_PROCESSED,
    )

    teacher_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Для mass — ученики в подзадачах; поля могут быть пустыми
    student_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    student_external_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Разовый перенос: исходное и новое время (свободный текст / дата-время)
    original_datetime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_datetime: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Постоянный перенос
    current_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schedule_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    nearest_lesson_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Массовый перенос
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    absence_period: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Подбор замены (увольнение / временное отсутствие)
    absence_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    return_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    initiator: Mapped[TicketInitiator] = mapped_column(
        Enum(TicketInitiator, name="ticket_initiator", native_enum=False),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    creator: Mapped[User] = relationship(
        "User",
        back_populates="created_tickets",
        foreign_keys=[creator_id],
    )
    assignee: Mapped[User | None] = relationship(
        "User",
        back_populates="assigned_tickets",
        foreign_keys=[assignee_id],
    )
    history: Mapped[list[TicketHistory]] = relationship(
        "TicketHistory",
        back_populates="ticket",
        order_by="TicketHistory.created_at",
        cascade="all, delete-orphan",
        foreign_keys="TicketHistory.ticket_id",
    )
    subtasks: Mapped[list[TicketSubtask]] = relationship(
        "TicketSubtask",
        back_populates="ticket",
        order_by="TicketSubtask.id",
        cascade="all, delete-orphan",
    )


class TicketSubtask(Base):
    """Подзадача по ученику: массовый перенос или подбор замены."""

    __tablename__ = "ticket_subtasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False, index=True)
    student_name: Mapped[str] = mapped_column(String(255), nullable=False)
    student_external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lesson_datetime: Mapped[str] = mapped_column(String(255), nullable=False)
    desired_new_datetime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # mass — обязательно; replacement — null
    required_action: Mapped[MassSubtaskAction | None] = mapped_column(
        Enum(MassSubtaskAction, name="mass_subtask_action", native_enum=False),
        nullable=True,
    )
    # предмет / расписание для подбора замены (расписание также в lesson_datetime)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replacement_stage: Mapped[ReplacementStage | None] = mapped_column(
        Enum(ReplacementStage, name="replacement_stage", native_enum=False),
        nullable=True,
    )
    current_candidate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    needs_senior_help: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status", native_enum=False),
        nullable=False,
        default=TicketStatus.NOT_PROCESSED,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    ticket: Mapped[Ticket] = relationship("Ticket", back_populates="subtasks")
    history: Mapped[list[TicketHistory]] = relationship(
        "TicketHistory",
        back_populates="subtask",
        order_by="TicketHistory.created_at",
        foreign_keys="TicketHistory.subtask_id",
    )
    proposals: Mapped[list[ReplacementProposal]] = relationship(
        "ReplacementProposal",
        back_populates="subtask",
        order_by="ReplacementProposal.created_at",
        cascade="all, delete-orphan",
    )


class ReplacementProposal(Base):
    """История предложенных кандидатов-замен по ученику."""

    __tablename__ = "replacement_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subtask_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_subtasks.id"), nullable=False, index=True
    )
    teacher_name: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[ProposalOutcome] = mapped_column(
        Enum(ProposalOutcome, name="proposal_outcome", native_enum=False),
        nullable=False,
        default=ProposalOutcome.ACTIVE,
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    subtask: Mapped[TicketSubtask] = relationship(
        "TicketSubtask", back_populates="proposals"
    )
    actor: Mapped[User | None] = relationship("User")


class TicketHistory(Base):
    """Журнал смены статусов и комментариев по заявке или подзадаче."""

    __tablename__ = "ticket_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False, index=True)
    subtask_id: Mapped[int | None] = mapped_column(
        ForeignKey("ticket_subtasks.id"), nullable=True, index=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    old_status: Mapped[TicketStatus | None] = mapped_column(
        Enum(TicketStatus, name="ticket_status", native_enum=False),
        nullable=True,
    )
    new_status: Mapped[TicketStatus | None] = mapped_column(
        Enum(TicketStatus, name="ticket_status", native_enum=False),
        nullable=True,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    ticket: Mapped[Ticket] = relationship(
        "Ticket",
        back_populates="history",
        foreign_keys=[ticket_id],
    )
    subtask: Mapped[TicketSubtask | None] = relationship(
        "TicketSubtask",
        back_populates="history",
        foreign_keys=[subtask_id],
    )
    actor: Mapped[User | None] = relationship("User")
