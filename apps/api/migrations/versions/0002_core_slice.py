"""core slice

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
    )
    op.create_table(
        "identity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_identity_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity")),
        sa.UniqueConstraint("provider", "external_id", name=op.f("uq_identity_provider")),
    )
    op.create_table(
        "interaction",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("source_language", sa.Text(), nullable=False),
        sa.Column("explanation_language", sa.Text(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=False),
        sa.Column("llm_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_interaction_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interaction")),
    )
    op.create_index(
        "ix_interaction_user_id_created_at",
        "interaction",
        ["user_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "correction",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interaction_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original", sa.Text(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction.id"],
            name=op.f("fk_correction_interaction_id_interaction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correction")),
    )
    op.create_index(
        op.f("ix_correction_interaction_id"), "correction", ["interaction_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_correction_interaction_id"), table_name="correction")
    op.drop_table("correction")
    op.drop_index("ix_interaction_user_id_created_at", table_name="interaction")
    op.drop_table("interaction")
    op.drop_table("identity")
    op.drop_table("app_user")
