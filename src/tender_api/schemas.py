"""Schemas de la API.

Las respuestas reutilizan los modelos de `tender_contracts` (fuente de verdad). Aquí se
definen los modelos de petición (lo que el cliente envía) y los conversores ORM → contrato.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field
from tender_contracts import (
    Recommendation,
    ScoreBreakdown,
    ScoreFactor,
    Source,
)
from tender_contracts import (
    Tender as TenderContract,
)
from tender_contracts import (
    TenderScore as ScoreContract,
)

from tender_api import models


class TenderCreate(BaseModel):
    """Alta/ingesta de una licitación (idempotente por source + source_id)."""

    source: Source
    source_id: str
    title: str
    summary: str | None = None
    cpv: list[str] = Field(default_factory=list)
    buyer: str | None = None
    budget_amount: float | None = Field(default=None, ge=0)
    currency: str = "EUR"
    publication_date: date | None = None
    deadline: datetime | None = None
    url: str | None = None


class ScoreUpsert(BaseModel):
    """Alta del score Go/No-Go de una licitación."""

    total: int = Field(ge=0, le=100)
    breakdown: ScoreBreakdown
    recommendation: Recommendation
    hard_rules: list[str] = Field(default_factory=list)
    factors: list[ScoreFactor] = Field(default_factory=list)
    model_version: str = "1.0.0"


class ActionCreate(BaseModel):
    action: str  # ActionType (interested/discarded/partner/…)
    actor: str | None = None
    note: str | None = None


class ActionRead(BaseModel):
    id: str
    tender_id: str
    action: str
    actor: str | None = None
    note: str | None = None
    created_at: datetime


class TenderWithScore(BaseModel):
    """Licitación + su último score (para el radar/top del bot)."""

    tender: TenderContract
    score: ScoreContract | None = None


class AskRequest(BaseModel):
    """Pregunta sobre el pliego de una licitación."""

    question: str
    top_k: int = 5


class DecisionCreate(BaseModel):
    """Decisión histórica sobre una licitación (alimenta el aprendizaje)."""

    decision: str  # GO / NO_GO / REVISAR / PARTNER / PRESENTADA / DESCARTAR
    outcome: str | None = None  # ganada / perdida / presentada / no_presentada / pendiente
    final_score: int | None = None
    awarded_company: str | None = None
    awarded_amount: float | None = None
    bid_amount: float | None = None
    reason: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


def tender_to_contract(row: models.Tender) -> TenderContract:
    return TenderContract(
        id=row.id,
        source=row.source,
        source_id=row.source_id,
        title=row.title,
        summary=row.summary,
        cpv=list(row.cpv or []),
        buyer=row.buyer,
        budget_amount=row.budget_amount,
        currency=row.currency,
        publication_date=row.publication_date,
        deadline=row.deadline,
        url=row.url,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def score_to_contract(row: models.TenderScore) -> ScoreContract:
    return ScoreContract(
        id=row.id,
        tender_id=row.tender_id,
        total=row.total,
        breakdown=row.breakdown,
        recommendation=row.recommendation,
        hard_rules=list(row.hard_rules or []),
        factors=list(row.factors or []),
        model_version=row.model_version,
        created_at=row.created_at,
    )
