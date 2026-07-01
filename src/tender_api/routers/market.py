"""Inteligencia de mercado (MVP-5): histórico de adjudicaciones públicas y su analítica.

Ingesta idempotente de adjudicaciones (`POST /api/market/awards`, protegida por RUN_TOKEN) y
consultas agregadas: competidores frecuentes, baja media (pricing), compradores recurrentes y
CPV estratégicos. Es analítica sobre datos públicos; no influye en el scoring del radar.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import Award, Tender

router = APIRouter(prefix="/api/market", tags=["market"])


class AwardIn(BaseModel):
    """Una adjudicación entrante (desde la ingesta de notas de formalización)."""

    source: str
    source_id: str
    title: str | None = None
    buyer: str | None = None
    cpv: list[str] = Field(default_factory=list)
    budget_amount: float | None = None
    awarded_amount: float | None = None
    awarded_supplier: str | None = None
    num_bidders: int | None = None
    award_date: str | None = None  # ISO date (YYYY-MM-DD)
    url: str | None = None


def _cpv_division(cpv: list[str] | None) -> str | None:
    """División CPV (2 primeros dígitos) del CPV principal — agrupa por categoría."""
    for c in cpv or []:
        digits = "".join(ch for ch in str(c) if ch.isdigit())
        if len(digits) >= 2:
            return digits[:2]
    return None


def _baja(budget: float | None, awarded: float | None) -> float | None:
    """Baja económica = (presupuesto - adjudicado) / presupuesto, en [0, 1]. None si no aplica."""
    if not budget or budget <= 0 or awarded is None or awarded < 0:
        return None
    b = (budget - awarded) / budget
    return round(max(0.0, min(1.0, b)), 4)


def _parse_date(value: str | None):
    if not value:
        return None
    from datetime import date

    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


@router.post("/awards")
def ingest_awards(
    payload: list[AwardIn],
    session: Session = Depends(get_session),
    x_run_token: str = Header(default=""),
) -> dict:
    """Alta/actualización idempotente de adjudicaciones (upsert por source+source_id)."""
    if settings.run_token and x_run_token != settings.run_token:
        raise HTTPException(401, "run token inválido")

    created = updated = 0
    for item in payload:
        if not item.source_id:
            continue
        row = session.scalar(
            select(Award).where(Award.source == item.source, Award.source_id == item.source_id)
        )
        if row is None:
            row = Award(source=item.source, source_id=item.source_id)
            session.add(row)
            created += 1
        else:
            updated += 1
        row.title = item.title
        row.buyer = item.buyer
        row.cpv = item.cpv or []
        row.cpv_division = _cpv_division(item.cpv)
        row.budget_amount = item.budget_amount
        row.awarded_amount = item.awarded_amount
        row.awarded_supplier = item.awarded_supplier
        row.num_bidders = item.num_bidders
        row.award_date = _parse_date(item.award_date)
        row.url = item.url
    session.commit()
    return {"created": created, "updated": updated, "total": created + updated}


def _filtered_awards(session: Session, cpv_division: str | None, buyer: str | None) -> list[Award]:
    stmt = select(Award)
    if cpv_division:
        stmt = stmt.where(Award.cpv_division == cpv_division)
    if buyer:
        stmt = stmt.where(Award.buyer == buyer)
    return list(session.scalars(stmt).all())


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


# --- Agregaciones puras (sin FastAPI): reutilizadas por las rutas y por overview/context. ---


def _competitors(rows: list[Award], limit: int) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"wins": 0, "total_awarded": 0.0, "bajas": []})
    for r in rows:
        if not r.awarded_supplier:
            continue
        a = agg[r.awarded_supplier]
        a["wins"] += 1
        a["total_awarded"] += r.awarded_amount or 0.0
        b = _baja(r.budget_amount, r.awarded_amount)
        if b is not None:
            a["bajas"].append(b)
    items = [
        {
            "supplier": name,
            "wins": v["wins"],
            "total_awarded": round(v["total_awarded"], 2),
            "avg_baja": _avg(v["bajas"]),
        }
        for name, v in agg.items()
    ]
    items.sort(key=lambda x: (x["wins"], x["total_awarded"]), reverse=True)
    return items[:limit]


def _pricing(rows: list[Award]) -> dict:
    bajas = [b for r in rows if (b := _baja(r.budget_amount, r.awarded_amount)) is not None]
    budgets = [r.budget_amount for r in rows if r.budget_amount]
    awarded = [r.awarded_amount for r in rows if r.awarded_amount]
    return {
        "count": len(rows),
        "with_baja": len(bajas),
        "avg_baja": _avg(bajas),
        "avg_budget": round(sum(budgets) / len(budgets), 2) if budgets else None,
        "avg_awarded": round(sum(awarded) / len(awarded), 2) if awarded else None,
    }


def _group_totals(rows: list[Award], key: str) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"awards": 0, "total_awarded": 0.0})
    for r in rows:
        k = getattr(r, key)
        if not k:
            continue
        a = agg[k]
        a["awards"] += 1
        a["total_awarded"] += r.awarded_amount or 0.0
    items = [
        {key: k, "awards": v["awards"], "total_awarded": round(v["total_awarded"], 2)}
        for k, v in agg.items()
    ]
    items.sort(key=lambda x: (x["awards"], x["total_awarded"]), reverse=True)
    return items


@router.get("/competitors")
def competitors(
    session: Session = Depends(get_session),
    cpv_division: str | None = Query(default=None),
    buyer: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Adjudicatarios más frecuentes (por CPV/órgano): contratos, importe total y baja media."""
    rows = _filtered_awards(session, cpv_division, buyer)
    return {"count": len(rows), "competitors": _competitors(rows, limit)}


@router.get("/pricing")
def pricing(
    session: Session = Depends(get_session),
    cpv_division: str | None = Query(default=None),
) -> dict:
    """Baja media y presupuestos por categoría (para anticipar la baja esperada)."""
    return _pricing(_filtered_awards(session, cpv_division, None))


@router.get("/buyers")
def buyers(
    session: Session = Depends(get_session),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Órganos compradores recurrentes: nº de adjudicaciones e importe total."""
    rows = list(session.scalars(select(Award)).all())
    return {"buyers": _group_totals(rows, "buyer")[:limit]}


@router.get("/cpv")
def cpv_volume(
    session: Session = Depends(get_session),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """CPV estratégicos por volumen: adjudicaciones e importe total por división."""
    rows = list(session.scalars(select(Award)).all())
    return {"divisions": _group_totals(rows, "cpv_division")[:limit]}


@router.get("/overview")
def overview(session: Session = Depends(get_session)) -> dict:
    """Resumen de mercado: totales, baja media global y líderes (competidor/comprador/CPV)."""
    rows = list(session.scalars(select(Award)).all())
    bajas = [b for r in rows if (b := _baja(r.budget_amount, r.awarded_amount)) is not None]
    top_competitor = _competitors(rows, 1)
    top_buyer = _group_totals(rows, "buyer")
    top_cpv = _group_totals(rows, "cpv_division")
    return {
        "awards": len(rows),
        "total_awarded": round(sum(r.awarded_amount or 0.0 for r in rows), 2),
        "avg_baja": _avg(bajas),
        "top_competitor": top_competitor[0] if top_competitor else None,
        "top_buyer": top_buyer[0] if top_buyer else None,
        "top_cpv_division": top_cpv[0] if top_cpv else None,
    }


def compute_context(session: Session, cpv: list[str] | None) -> dict:
    """Contexto competitivo de una categoría CPV (reutilizado por la ruta y por los borradores)."""
    division = _cpv_division(cpv)
    rows = _filtered_awards(session, division, None)
    price = _pricing(rows)
    return {
        "cpv_division": division,
        "sample_size": len(rows),
        "likely_winners": _competitors(rows, 5),
        "expected_baja": price["avg_baja"],
        "avg_awarded": price["avg_awarded"],
    }


@router.get("/tender/{tender_id}/context")
def tender_market_context(tender_id: str, session: Session = Depends(get_session)) -> dict:
    """Contexto competitivo de UNA licitación: quién suele ganar su categoría y baja esperada."""
    tender = session.get(Tender, tender_id)
    if tender is None:
        raise HTTPException(404, "Licitación no encontrada")
    return compute_context(session, tender.cpv)
