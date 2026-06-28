"""Perfil Keedio editable (keywords/CPV/áreas) — fila única, consumido por ingesta y scoring."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tender_api.database import get_session
from tender_api.models import ScoringProfile

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
