"""stage4 dismissal / temporary absence / replacement

Revision ID: 0004_stage4_replacement
Revises: 0003_stage3_mass
Create Date: 2026-11-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_stage4_replacement"
down_revision: Union[str, None] = "0003_stage3_mass"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite: Enum as VARCHAR — dismissal / temporary_absence ok without ALTER ENUM.
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("absence_start_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("return_date", sa.Date(), nullable=True))

    with op.batch_alter_table("ticket_subtasks") as batch:
        batch.alter_column(
            "required_action",
            existing_type=sa.Enum(
                "propose_transfer",
                "agree_cancel",
                "temp_replacement",
                "clarify_parent",
                name="mass_subtask_action",
                native_enum=False,
            ),
            nullable=True,
        )
        batch.add_column(sa.Column("subject", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column(
                "replacement_stage",
                sa.Enum(
                    "searching",
                    "found",
                    "offered",
                    "agreed",
                    "closed_no_continue",
                    name="replacement_stage",
                    native_enum=False,
                ),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column("current_candidate_name", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "needs_senior_help",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("close_reason", sa.Text(), nullable=True))

    op.create_table(
        "replacement_proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subtask_id", sa.Integer(), nullable=False),
        sa.Column("teacher_name", sa.String(length=255), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "active",
                "accepted",
                "rejected_search_another",
                "superseded",
                name="proposal_outcome",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["subtask_id"], ["ticket_subtasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_replacement_proposals_subtask_id",
        "replacement_proposals",
        ["subtask_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_replacement_proposals_subtask_id", table_name="replacement_proposals"
    )
    op.drop_table("replacement_proposals")

    with op.batch_alter_table("ticket_subtasks") as batch:
        batch.drop_column("close_reason")
        batch.drop_column("needs_senior_help")
        batch.drop_column("current_candidate_name")
        batch.drop_column("replacement_stage")
        batch.drop_column("subject")
        batch.alter_column(
            "required_action",
            existing_type=sa.Enum(
                "propose_transfer",
                "agree_cancel",
                "temp_replacement",
                "clarify_parent",
                name="mass_subtask_action",
                native_enum=False,
            ),
            nullable=False,
        )

    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("return_date")
        batch.drop_column("absence_start_date")
