"""Inteligencia de mercado (MVP-5): histórico de adjudicaciones públicas y su analítica.

Ingesta idempotente de adjudicaciones (`POST /api/market/awards`, protegida por RUN_TOKEN) y
consultas agregadas: competidores frecuentes, baja media (pricing), compradores recurrentes y
CPV estratégicos. Es analítica sobre datos públicos; no influye en el scoring del radar.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
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
    """Baja económica = (presupuesto - adjudicado) / presupuesto. None si no es una baja válida.

    Fuente única de verdad usada por overview/pricing/competidores/contexto. Devuelve None si falta
    algún importe o si `adjudicado > presupuesto` (mismatch de escala en marcos o sobrecoste) — así
    esos casos se EXCLUYEN de forma consistente en todos los agregados (no cuentan como baja 0%).
    """
    if not budget or budget <= 0 or awarded is None or awarded < 0 or awarded > budget:
        return None
    return round((budget - awarded) / budget, 4)


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


_LEGAL_FORM = re.compile(r"\b(SLU|SAU|SLL|SLP|SL|SA|SCA|SCL|SCOOP|AIE|UTE)\b")


def _norm_supplier(name: str) -> str:
    """Clave para agrupar adjudicatarios: mayúsculas, sin puntuación ni forma jurídica.

    Fusiona variantes de la misma empresa ("SEIDOR CONSULTING, SL" == "SEIDOR CONSULTING, S.L.").
    """
    s = name.upper().replace(".", "").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = _LEGAL_FORM.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or name.upper()


# --- Agregaciones puras (sin FastAPI): reutilizadas por las rutas y por overview/context. ---


def _competitors(rows: list[Award], limit: int) -> list[dict]:
    # Agrupa por razón social normalizada (fusiona SL/S.L./…) mostrando el primer nombre visto.
    agg: dict[str, dict] = defaultdict(
        lambda: {"wins": 0, "total_awarded": 0.0, "bajas": [], "name": None}
    )
    # Total del mercado (adjudicaciones con adjudicatario) para la CUOTA por importe.
    market_total = sum(r.awarded_amount or 0.0 for r in rows if r.awarded_supplier)
    for r in rows:
        if not r.awarded_supplier:
            continue
        a = agg[_norm_supplier(r.awarded_supplier)]
        if a["name"] is None:
            a["name"] = r.awarded_supplier
        a["wins"] += 1
        a["total_awarded"] += r.awarded_amount or 0.0
        b = _baja(r.budget_amount, r.awarded_amount)
        if b is not None:
            a["bajas"].append(b)
    items = [
        {
            "supplier": v["name"],
            "wins": v["wins"],
            "total_awarded": round(v["total_awarded"], 2),
            "avg_baja": _avg(v["bajas"]),
            # Cuota de mercado estimada por importe adjudicado (dentro del filtro CPV/órgano).
            "share": round(v["total_awarded"] / market_total, 4) if market_total else None,
        }
        for v in agg.values()
    ]
    items.sort(key=lambda x: (x["wins"], x["total_awarded"]), reverse=True)
    return items[:limit]


def _pricing(rows: list[Award]) -> dict:
    # Medios sobre el conjunto con baja VÁLIDA (misma definición que _baja) → comparables y
    # coherentes con la baja media (adjudicado medio <= presupuesto medio).
    pairs = [
        (r.budget_amount, r.awarded_amount, b)
        for r in rows
        if (b := _baja(r.budget_amount, r.awarded_amount)) is not None
    ]
    budgets = [p[0] for p in pairs]
    awarded = [p[1] for p in pairs]
    return {
        "count": len(rows),
        "with_baja": len(pairs),
        "avg_baja": _avg([p[2] for p in pairs]),
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


@router.get("/awards.csv")
def awards_csv(
    session: Session = Depends(get_session),
    cpv_division: str | None = Query(default=None),
) -> Response:
    """Exporta las adjudicaciones (con baja calculada) a CSV para informes/Excel."""
    rows = _filtered_awards(session, cpv_division, None)
    rows.sort(key=lambda r: r.award_date.isoformat() if r.award_date else "", reverse=True)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        ["source_id", "buyer", "cpv_division", "awarded_supplier", "budget_amount",
         "awarded_amount", "baja_%", "award_date", "title", "url"]
    )
    for r in rows:
        b = _baja(r.budget_amount, r.awarded_amount)
        writer.writerow(
            [
                r.source_id, r.buyer or "", r.cpv_division or "", r.awarded_supplier or "",
                "" if r.budget_amount is None else r.budget_amount,
                "" if r.awarded_amount is None else r.awarded_amount,
                "" if b is None else round(b * 100, 1),
                r.award_date.isoformat() if r.award_date else "",
                r.title or "", r.url or "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="adjudicaciones.csv"'},
    )


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
