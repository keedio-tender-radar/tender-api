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

__all__ = [
    "Tender",
    "TenderScore",
    "TenderAction",
    "TenderDecision",
    "TenderDocument",
    "GeneratedDocument",
]


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
    # Si es duplicada de otra fuente, apunta a la licitación "canónica" (se excluye de las vistas).
    duplicate_of: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
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
    files: Mapped[list[TenderDocument]] = relationship(
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
    summary: Mapped[str | None] = mapped_column(String, nullable=True)  # resumen del análisis IA
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


class ScoringProfile(Base):
    """Perfil Keedio editable (keywords/CPV/áreas) — fila única, sin redeploy."""

    __tablename__ = "scoring_profile"

    id: Mapped[str] = mapped_column(String, primary_key=True, default="default")
    keywords_positive: Mapped[list] = mapped_column(JSON, default=list)
    keywords_negative: Mapped[list] = mapped_column(JSON, default=list)
    cpv_preferred: Mapped[list] = mapped_column(JSON, default=list)
    cpv_excluded: Mapped[list] = mapped_column(JSON, default=list)
    areas: Mapped[list] = mapped_column(JSON, default=list)
    team: Mapped[list] = mapped_column(JSON, default=list)  # roles del equipo (organigrama)
    project_months: Mapped[int] = mapped_column(Integer, default=6)  # duración (cronograma)
    hourly_rate: Mapped[float] = mapped_column(Float, default=45.0)  # €/hora (estimación)
    margin: Mapped[float] = mapped_column(Float, default=0.2)  # margen comercial (0..1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class TenderDocument(Base):
    """Binario original del expediente (PCAP/PPT/anexos) almacenado en S3/MinIO."""

    __tablename__ = "tender_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    folder: Mapped[str] = mapped_column(String, default="00_originales")
    filename: Mapped[str] = mapped_column(String)
    storage_key: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="files")


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


class DailySnapshot(Base):
    """Foto diaria de las licitaciones activas top (histórico web+Telegram)."""

    __tablename__ = "daily_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TenderNote(Base):
    """Nota/comentario del equipo sobre una licitación (colaboración)."""

    __tablename__ = "tender_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TenderAction(Base):
    __tablename__ = "tender_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("tenders.id"), index=True)
    action: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tender: Mapped[Tender] = relationship(back_populates="actions")
