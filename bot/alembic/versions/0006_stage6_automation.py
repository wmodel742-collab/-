"""stage6 automation notification_logs

Revision ID: 0006_stage6_automation
Revises: 0005_stage5_schedule
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_stage6_automation"
down_revision: Union[str, None] = "0005_stage5_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("target_key", sa.String(length=128), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_logs_kind", "notification_logs", ["kind"], unique=False)
    op.create_index(
        "ix_notification_logs_target_key",
        "notification_logs",
        ["target_key"],
        unique=False,
    )
    op.create_index(
        "ix_notification_logs_created_at",
        "notification_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notification_logs_created_at", table_name="notification_logs")
    op.drop_index("ix_notification_logs_target_key", table_name="notification_logs")
    op.drop_index("ix_notification_logs_kind", table_name="notification_logs")
    op.drop_table("notification_logs")
