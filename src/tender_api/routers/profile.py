"""Perfil Keedio editable (keywords/CPV/áreas) — fila única, consumido por ingesta y scoring."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.database import get_session
from tender_api.models import RunLog, ScoringProfile, TenderDecision, TenderScore

_WON = {"ganada", "won", "adjudicada", "adjudicado"}
_LOST = {"perdida", "lost", "no_adjudicada", "desestimada"}

router = APIRouter(prefix="/api/profile", tags=["profile"])

DEFAULTS = {
    "keywords_positive": [
        "datos", "big data", "analítica", "inteligencia artificial", "machine learning",
        "integración", "api", "cloud", "kubernetes", "devops", "plataforma", "rag", "etl",
        "ciberseguridad", "interoperabilidad",
    ],
    "keywords_negative": [
        "obra civil", "construcción", "limpieza", "vigilancia", "catering", "jardinería",
        "mobiliario", "transporte",
    ],
    "cpv_preferred": ["72", "48"],
    "cpv_excluded": ["45", "90", "79710000"],
    "areas": [
        "Big Data", "IA", "Machine Learning", "APIs", "Integración", "Cloud", "Ciberseguridad",
    ],
    "team": ["Arquitecto/a", "Equipo desarrollo", "QA / Pruebas", "Soporte"],
    "project_months": 6,
    "hourly_rate": 45.0,
    "margin": 0.2,
    "go_threshold": 80,
    "revisar_threshold": 40,
}


class ProfileUpdate(BaseModel):
    keywords_positive: list[str] | None = None
    keywords_negative: list[str] | None = None
    cpv_preferred: list[str] | None = None
    cpv_excluded: list[str] | None = None
    areas: list[str] | None = None
    team: list[str] | None = None
    project_months: int | None = None
    hourly_rate: float | None = None
    margin: float | None = None
    go_threshold: int | None = None
    revisar_threshold: int | None = None


def _get_or_create(session: Session) -> ScoringProfile:
    row = session.get(ScoringProfile, "default")
    if not row:
        row = ScoringProfile(id="default", **DEFAULTS)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _serialize(r: ScoringProfile) -> dict:
    return {
        "keywords_positive": list(r.keywords_positive or []),
        "keywords_negative": list(r.keywords_negative or []),
        "cpv_preferred": list(r.cpv_preferred or []),
        "cpv_excluded": list(r.cpv_excluded or []),
        "areas": list(r.areas or []),
        "team": list(r.team or []) or DEFAULTS["team"],
        "project_months": r.project_months or DEFAULTS["project_months"],
        "hourly_rate": r.hourly_rate or DEFAULTS["hourly_rate"],
        "margin": r.margin if r.margin is not None else DEFAULTS["margin"],
        "go_threshold": r.go_threshold or DEFAULTS["go_threshold"],
        "revisar_threshold": r.revisar_threshold or DEFAULTS["revisar_threshold"],
    }


@router.get("")
def get_profile(session: Session = Depends(get_session)) -> dict:
    return _serialize(_get_or_create(session))


@router.put("")
def put_profile(payload: ProfileUpdate, session: Session = Depends(get_session)) -> dict:
    row = _get_or_create(session)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    session.commit()
    session.refresh(row)
    return _serialize(row)


def _score_for(session: Session, dec: TenderDecision) -> int | None:
    if dec.final_score is not None:
        return dec.final_score
    s = session.scalars(
        select(TenderScore)
        .where(TenderScore.tender_id == dec.tender_id)
        .order_by(TenderScore.created_at.desc())
    ).first()
    return s.total if s else None


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, round((len(s) - 1) * pct)))
    return float(s[k])


@router.post("/recalibrate")
def recalibrate(apply: bool = True, session: Session = Depends(get_session)) -> dict:
    """Ajusta los umbrales GO/REVISAR a partir del histórico de decisiones ganadas/perdidas.

    GO ≈ percentil 20 de los scores de las ganadas (capturarlas como GO); REVISAR por debajo,
    sobre la franja de las perdidas. Conservador: exige mínimos y mantiene si no hay señal.
    """
    decisions = session.scalars(select(TenderDecision)).all()
    won, lost = [], []
    for d in decisions:
        outcome = (d.outcome or "").strip().lower()
        sc = _score_for(session, d)
        if sc is None:
            continue
        if outcome in _WON:
            won.append(sc)
        elif outcome in _LOST:
            lost.append(sc)

    row = _get_or_create(session)
    current = {
        "go_threshold": row.go_threshold or 80,
        "revisar_threshold": row.revisar_threshold or 40,
    }
    if len(won) < 3:
        session.add(RunLog(
            job="recalibracion", status="ok", count=len(won),
            detail=f"sin cambios: {len(won)} ganadas (mín. 3)",
        ))
        session.commit()
        return {"applied": False, "reason": "insuficientes decisiones ganadas (mín. 3)",
                "won": len(won), "lost": len(lost), **current}

    go = int(max(55, min(85, round(_percentile(won, 0.2)))))
    lost_mid = _percentile(lost, 0.5) if lost else go - 25
    revisar = int(max(25, min(go - 10, round(lost_mid))))
    proposed = {"go_threshold": go, "revisar_threshold": revisar}

    if apply:
        row.go_threshold, row.revisar_threshold = go, revisar
    session.add(RunLog(
        job="recalibracion", status="ok", count=len(won),
        detail=f"{'aplicado' if apply else 'propuesto'} GO≥{go}/Revisar≥{revisar} "
               f"({len(won)} ganadas, {len(lost)} perdidas)",
    ))
    session.commit()
    return {"applied": apply, "won": len(won), "lost": len(lost),
            "previous": current, **proposed}
