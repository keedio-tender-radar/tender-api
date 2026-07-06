"""Endpoints de scoring de una licitación."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from tender_contracts import TenderScore as ScoreContract

from tender_api.database import get_session
from tender_api.models import Tender, TenderScore
from tender_api.schemas import ScoreUpsert, score_to_contract

router = APIRouter(prefix="/api/tenders", tags=["scores"])


@router.put("/{tender_id}/score", response_model=ScoreContract, status_code=201)
def put_score(tender_id: str, payload: ScoreUpsert, session: Session = Depends(get_session)):
    """Registra el score Go/No-Go de la licitación y marca su estado como 'scored'."""
    tender = session.get(Tender, tender_id)
    if not tender:
        raise HTTPException(404, "Tender not found")
    if payload.total != payload.breakdown.total():
        raise HTTPException(
            422, f"total ({payload.total}) != suma del breakdown ({payload.breakdown.total()})"
        )

    row = TenderScore(
        tender_id=tender_id,
        total=payload.total,
        breakdown=payload.breakdown.model_dump(),
        recommendation=payload.recommendation.value,
        hard_rules=list(payload.hard_rules),
        factors=[f.model_dump() for f in payload.factors],
        summary=payload.summary,
        model_version=payload.model_version,
    )
    session.add(row)
    tender.status = "scored"
    # Si la licitación llegó sin resumen (p. ej. TED pone summary=None), adopta el del análisis
    # para que las tarjetas y el listado muestren una descripción real, no solo el título.
    if payload.summary and not (tender.summary or "").strip():
        tender.summary = payload.summary
    session.commit()
    session.refresh(row)
    return score_to_contract(row)


@router.get("/{tender_id}/score", response_model=ScoreContract)
def get_score(tender_id: str, session: Session = Depends(get_session)):
    """Devuelve el último score de la licitación."""
    row = session.scalars(
        select(TenderScore)
        .where(TenderScore.tender_id == tender_id)
        .order_by(TenderScore.created_at.desc())
    ).first()
    if not row:
        raise HTTPException(404, "No score for this tender")
    return score_to_contract(row)
