"""initial schema: participants and answers

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pseudonym", sa.String(16), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, unique=True),
        sa.Column("voice_order", json_type, nullable=False),
        sa.Column("state", json_type, nullable=False),
        sa.Column("current_prompt", json_type, nullable=True),
        sa.Column("native_english", sa.Boolean(), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_participants_token_hash", "participants", ["token_hash"], unique=True)

    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participants.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("prompt_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("block", sa.String(20), nullable=True),
        sa.Column("voice", sa.String(20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("modality", sa.String(5), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("asr_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_answers_participant_id", "answers", ["participant_id"])
    op.create_index("ix_answers_participant_created", "answers", ["participant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("answers")
    op.drop_table("participants")
