"""stage5 curator schedule 2/2

Revision ID: 0005_stage5_schedule
Revises: 0004_stage4_replacement
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_stage5_schedule"
down_revision: Union[str, None] = "0004_stage4_replacement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schedule_cycles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("first_curator_id", sa.Integer(), nullable=False),
        sa.Column("second_curator_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["first_curator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["second_curator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_cycles_effective_from",
        "schedule_cycles",
        ["effective_from"],
        unique=False,
    )

    op.create_table(
        "schedule_day_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("curator_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["curator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day"),
    )
    op.create_index(
        "ix_schedule_day_overrides_day",
        "schedule_day_overrides",
        ["day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_day_overrides_day", table_name="schedule_day_overrides")
    op.drop_table("schedule_day_overrides")
    op.drop_index("ix_schedule_cycles_effective_from", table_name="schedule_cycles")
    op.drop_table("schedule_cycles")
