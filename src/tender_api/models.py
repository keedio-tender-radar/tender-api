"""Modelos SQLAlchemy del núcleo (MVP).

Persistencia de licitaciones, scores y acciones. Los formatos de intercambio (API/eventos)
viven en `tender_contracts`; aquí está el almacenamiento. Tipos elegidos para funcionar tanto
en SQLite (tests) como en PostgreSQL (despliegue).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_tender_source"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    cpv: Mapped[list] = mapped_column(JSON, default=list)
    buyer: Mapped[str | None] = mapped_column(String, nullable=True)
    budget_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="EUR")
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="discovered", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    scores: Mapped[list[TenderScore]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    actions: Mapped[list[TenderAction]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[TenderDecision]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    documents: Mapped[list[GeneratedDocument]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )


class TenderScore(Base):
    __tablename__ = "tender_scores"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    total: Mapped[int] = mapped_column(Integer)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    recommendation: Mapped[str] = mapped_column(String, index=True)
    hard_rules: Mapped[list] = mapped_column(JSON, default=list)
    factors: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String, default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="scores")


class TenderDecision(Base):
    """Decisión histórica sobre una licitación (alimenta el aprendizaje y el histórico)."""

    __tablename__ = "tender_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    decision: Mapped[str] = mapped_column(String, index=True)  # GO/NO_GO/REVISAR/PARTNER/...
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # ganada/perdida/...
    final_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awarded_company: Mapped[str | None] = mapped_column(String, nullable=True)
    awarded_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="decisions")


class GeneratedDocument(Base):
    """Borrador de oferta generado (Go/No-Go, memoria técnica, matriz, checklist…)."""

    __tablename__ = "generated_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    generated_by: Mapped[str] = mapped_column(String, default="rule-based")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="documents")


class TenderAction(Base):
    __tablename__ = "tender_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    action: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="actions")
