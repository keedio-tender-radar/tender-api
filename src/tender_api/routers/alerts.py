"""Búsquedas guardadas / alertas a medida (p. ej. "CPV 72 con score≥70 y >200k€").

CRUD de alertas + endpoint de coincidencias (licitaciones activas recientes que cumplen alguna
alerta). Lo consume el dashboard y el bot (envío a Telegram).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.database import get_session
from tender_api.models import SavedAlert, Tender, TenderScore
from tender_api.schemas import score_to_contract, tender_to_contract
from tender_api.services import semaphore

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertCreate(BaseModel):
    name: str
    min_score: int | None = None
    cpv_prefix: str | None = None
    min_budget: float | None = None
    source: str | None = None
    traffic_light: str | None = None
    q: str | None = None
    enabled: bool = True


def _serialize(a: SavedAlert) -> dict:
    return {
        "id": a.id, "name": a.name, "enabled": a.enabled, "min_score": a.min_score,
        "cpv_prefix": a.cpv_prefix, "min_budget": a.min_budget, "source": a.source,
        "traffic_light": a.traffic_light, "q": a.q,
    }


@router.get("")
def list_alerts(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(SavedAlert).order_by(SavedAlert.created_at)).all()
    return [_serialize(a) for a in rows]


@router.post("", status_code=201)
def create_alert(payload: AlertCreate, session: Session = Depends(get_session)) -> dict:
    a = SavedAlert(**payload.model_dump())
    session.add(a)
    session.commit()
    session.refresh(a)
    return _serialize(a)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: str, session: Session = Depends(get_session)) -> None:
    a = session.get(SavedAlert, alert_id)
    if a:
        session.delete(a)
        session.commit()


def _matches(t: Tender, score: TenderScore | None, a: SavedAlert) -> bool:
    total = score.total if score else None
    if a.min_score is not None and (total is None or total < a.min_score):
        return False
    if a.cpv_prefix and not any(str(c).startswith(a.cpv_prefix) for c in (t.cpv or [])):
        return False
    if a.min_budget is not None and (t.budget_amount is None or t.budget_amount < a.min_budget):
        return False
    if a.source and t.source != a.source:
        return False
    if a.q and a.q.lower() not in (t.title or "").lower():
        return False
    if a.traffic_light:
        days = semaphore.days_remaining(t.deadline)
        rec = score.recommendation if score else None
        light = semaphore.traffic_light(total, rec, days)["light"]
        if light != a.traffic_light:
            return False
    return True


@router.get("/matches")
def alert_matches(
    session: Session = Depends(get_session),
    days: int = Query(default=2, ge=1, le=30),
) -> list[dict]:
    """Licitaciones activas creadas en los últimos `days` que cumplen alguna alerta habilitada."""
    alerts = session.scalars(select(SavedAlert).where(SavedAlert.enabled.is_(True))).all()
    if not alerts:
        return []
    since = datetime.now(UTC) - timedelta(days=days)
    tenders = session.scalars(
        select(Tender).where(Tender.duplicate_of.is_(None), Tender.created_at >= since)
    ).all()
    out = []
    for t in tenders:
        score = session.scalars(
            select(TenderScore)
            .where(TenderScore.tender_id == t.id)
            .order_by(TenderScore.created_at.desc())
        ).first()
        matched = [a.name for a in alerts if _matches(t, score, a)]
        if matched:
            out.append({
                "tender": tender_to_contract(t).model_dump(mode="json"),
                "score": score_to_contract(score).model_dump(mode="json") if score else None,
                "alerts": matched,
            })
    return out
