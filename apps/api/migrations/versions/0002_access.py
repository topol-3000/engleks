"""access

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("max_uses", sa.Integer(), server_default="1", nullable=False),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_code")),
        sa.UniqueConstraint("code", name=op.f("uq_invite_code_code")),
    )
    op.create_table(
        "rate_limit",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_rate_limit_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_rate_limit")),
    )
    op.create_table(
        "usage_daily",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.Column("cost", sa.Numeric(precision=14, scale=8), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_usage_daily_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("user_id", "day", name=op.f("pk_usage_daily")),
    )
    op.add_column(
        "app_user", sa.Column("status", sa.Text(), server_default="active", nullable=False)
    )
    op.add_column("app_user", sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True))
    op.add_column("app_user", sa.Column("daily_token_budget", sa.Integer(), nullable=True))
    op.add_column("app_user", sa.Column("invite_code_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_app_user_invite_code_id_invite_code"),
        "app_user",
        "invite_code",
        ["invite_code_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_app_user_invite_code_id_invite_code"), "app_user", type_="foreignkey"
    )
    op.drop_column("app_user", "invite_code_id")
    op.drop_column("app_user", "daily_token_budget")
    op.drop_column("app_user", "rate_limit_per_minute")
    op.drop_column("app_user", "status")
    op.drop_table("usage_daily")
    op.drop_table("rate_limit")
    op.drop_table("invite_code")
