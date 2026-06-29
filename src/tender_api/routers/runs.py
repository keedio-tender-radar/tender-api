"""Observabilidad: registro de ejecuciones de jobs/cron + alerta a Telegram en fallo.

Los servicios (ingesta, análisis, digest…) reportan aquí su resultado con POST /api/runs
(protegido por RUN_TOKEN). El dashboard lee GET /api/runs para la página de Estado.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import RunLog

router = APIRouter(prefix="/api/runs", tags=["runs"])
log = logging.getLogger("tender_api")


class RunCreate(BaseModel):
    job: str
    status: str = "ok"  # ok | error
    detail: str | None = None
    count: int | None = None


def _notify_telegram(text: str) -> None:
    """Avisa al chat configurado si una ejecución falla. No es fatal."""
    token = getattr(settings, "telegram_bot_token", "")
    chat = getattr(settings, "telegram_chat_id", "")
    if not token or not chat:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=10,
        )
    except httpx.HTTPError as exc:  # noqa: BLE001
        log.warning("runs: alerta telegram falló: %s", exc)


@router.post("", status_code=201)
def create_run(
    payload: RunCreate,
    session: Session = Depends(get_session),
    x_run_token: str = Header(default=""),
) -> dict:
    if settings.run_token and x_run_token != settings.run_token:
        raise HTTPException(401, "run token inválido")
    row = RunLog(job=payload.job, status=payload.status, detail=payload.detail, count=payload.count)
    session.add(row)
    session.commit()
    if payload.status == "error":
        _notify_telegram(f"⚠️ Tender Radar — fallo en «{payload.job}»: {payload.detail or 's/d'}")
    return {"id": row.id, "ok": True}


@router.get("")
def list_runs(
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    rows = session.scalars(
        select(RunLog).order_by(RunLog.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "job": r.job,
            "status": r.status,
            "detail": r.detail,
            "count": r.count,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/summary")
def runs_summary(session: Session = Depends(get_session)) -> dict:
    """Último estado por job (para la página de Estado)."""
    rows = session.scalars(select(RunLog).order_by(RunLog.created_at.desc()).limit(200)).all()
    latest: dict[str, dict] = {}
    for r in rows:
        if r.job not in latest:
            latest[r.job] = {
                "job": r.job,
                "status": r.status,
                "detail": r.detail,
                "count": r.count,
                "at": r.created_at.isoformat() if r.created_at else None,
            }
    return {"jobs": list(latest.values())}
