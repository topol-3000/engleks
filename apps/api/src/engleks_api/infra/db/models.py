import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from engleks_api.infra.db.base import Base

# Enum columns are TEXT, validated by the domain enums: native Postgres enums need a
# migration per value, and autogenerate does not detect value changes.


class UserRow(Base):
    # Not "user": reserved in Postgres.
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityRow(Base):
    __tablename__ = "identity"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    provider: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InteractionRow(Base):
    __tablename__ = "interaction"
    __table_args__ = (Index("ix_interaction_user_id_created_at", "user_id", desc("created_at")),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    mode: Mapped[str] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(Text)
    explanation_language: Mapped[str] = mapped_column(Text)
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    llm_response: Mapped[dict[str, object]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))


class CorrectionRow(Base):
    """Not written since process_text.v3, which returns lessons instead of changes."""

    __tablename__ = "correction"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interaction.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    original: Mapped[str] = mapped_column(Text)
    replacement: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
