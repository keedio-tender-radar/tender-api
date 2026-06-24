"""Endpoints de licitaciones: ingesta idempotente, listado, ficha, top y urgentes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tender_contracts import Tender as TenderContract

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import Tender, TenderScore
from tender_api.schemas import TenderCreate, TenderWithScore, score_to_contract, tender_to_contract

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


def _latest_score(session: Session, tender_id: str) -> TenderScore | None:
    return session.scalars(
        select(TenderScore)
        .where(TenderScore.tender_id == tender_id)
        .order_by(TenderScore.created_at.desc())
    ).first()


def _get_or_404(session: Session, tender_id: str) -> Tender:
    row = session.get(Tender, tender_id)
    if not row:
        raise HTTPException(404, "Tender not found")
    return row


@router.post("", response_model=TenderContract, status_code=201)
def ingest_tender(payload: TenderCreate, session: Session = Depends(get_session)):
    """Crea o actualiza una licitación. Idempotente por (source, source_id)."""
    existing = session.scalars(
        select(Tender).where(
            Tender.source == payload.source, Tender.source_id == payload.source_id
        )
    ).first()

    data = payload.model_dump()
    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        row = existing
    else:
        row = Tender(**data)
        session.add(row)
    session.commit()
    session.refresh(row)
    return tender_to_contract(row)


@router.get("", response_model=list[TenderContract])
def list_tenders(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Búsqueda por título (subcadena)."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Tender).order_by(Tender.created_at.desc())
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))
    rows = session.scalars(stmt.offset(offset).limit(limit)).all()
    return [tender_to_contract(r) for r in rows]


@router.get("/search", response_model=list[TenderWithScore])
def search_tenders(
    session: Session = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Búsqueda por título (subcadena)."),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Listado con búsqueda/paginación que incluye el último score de cada licitación."""
    stmt = select(Tender).order_by(Tender.created_at.desc())
    if status:
        stmt = stmt.where(Tender.status == status)
    if q:
        stmt = stmt.where(Tender.title.ilike(f"%{q}%"))
    rows = session.scalars(stmt.offset(offset).limit(limit)).all()
    out = []
    for r in rows:
        score = _latest_score(session, r.id)
        out.append(
            TenderWithScore(
                tender=tender_to_contract(r),
                score=score_to_contract(score) if score else None,
            )
        )
    return out


@router.get("/top", response_model=list[TenderWithScore])
def top_tenders(
    session: Session = Depends(get_session),
    limit: int = Query(default=5, ge=1, le=50),
):
    """Mejores oportunidades por último score (desc). Solo licitaciones ya puntuadas."""
    scored = []
    for tender in session.scalars(select(Tender)).all():
        score = _latest_score(session, tender.id)
        if score is not None:
            scored.append((tender, score))
    scored.sort(key=lambda pair: pair[1].total, reverse=True)
    return [
        TenderWithScore(tender=tender_to_contract(t), score=score_to_contract(s))
        for t, s in scored[:limit]
    ]


@router.get("/urgent", response_model=list[TenderWithScore])
def urgent_tenders(
    session: Session = Depends(get_session),
    days: int | None = Query(default=None, ge=1, le=90),
):
    """Licitaciones con cierre dentro de `days` (por defecto settings.urgent_days)."""
    horizon_days = days or settings.urgent_days
    now = datetime.now(UTC)
    limit_dt = now + timedelta(days=horizon_days)

    rows = session.scalars(
        select(Tender)
        .where(Tender.deadline.is_not(None))
        .where(Tender.deadline >= now)
        .where(Tender.deadline <= limit_dt)
        .order_by(Tender.deadline.asc())
    ).all()
    return [
        TenderWithScore(
            tender=tender_to_contract(t),
            score=(s := _latest_score(session, t.id)) and score_to_contract(s),
        )
        for t in rows
    ]


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    """Agregados para el panel de visión general (conteos y presupuesto de oportunidades)."""

    def _grouped(column) -> dict[str, int]:
        rows = session.execute(select(column, func.count()).group_by(column)).all()
        return {str(k): int(n) for k, n in rows}

    total = session.scalar(select(func.count()).select_from(Tender)) or 0
    by_status = _grouped(Tender.status)
    by_source = _grouped(Tender.source)
    by_recommendation = _grouped(TenderScore.recommendation)

    go_budget_total = (
        session.scalar(
            select(func.coalesce(func.sum(Tender.budget_amount), 0.0))
            .select_from(Tender)
            .join(TenderScore, TenderScore.tender_id == Tender.id)
            .where(TenderScore.recommendation == "go")
        )
        or 0.0
    )

    return {
        "total": total,
        "by_status": by_status,
        "by_source": by_source,
        "by_recommendation": by_recommendation,
        "go_count": by_recommendation.get("go", 0),
        "go_budget_total": float(go_budget_total),
    }


@router.get("/{tender_id}", response_model=TenderContract)
def get_tender(tender_id: str, session: Session = Depends(get_session)):
    return tender_to_contract(_get_or_404(session, tender_id))
