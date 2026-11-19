from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Participant(Base):
    """A pseudonymous participant. No names or contact details are stored."""

    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    pseudonym: Mapped[str] = mapped_column(String(16), unique=True)
    # SHA-256 of the access token in the participant's link; the token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, unique=True)  # selects the Latin-square row
    voice_order: Mapped[list[str]] = mapped_column(JSONType)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    current_prompt: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    native_english: Mapped[bool | None] = mapped_column(nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    answers: Mapped[list[Answer]] = relationship(
        back_populates="participant", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def status(self) -> str:
        if self.withdrawn_at:
            return "withdrawn"
        if self.completed_at:
            return "completed"
        if self.consent_at:
            return "in_progress"
        return "invited"


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_participant_created", "participant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    prompt_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))  # warmup | question
    block: Mapped[str | None] = mapped_column(String(20), nullable=True)
    voice: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)
    prompt_text: Mapped[str] = mapped_column(Text)
    transcript: Mapped[str] = mapped_column(Text)
    modality: Mapped[str] = mapped_column(String(5))  # voice | text
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    asr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="answers")


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
