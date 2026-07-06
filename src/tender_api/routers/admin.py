"""Administración: backup de la base de datos a InsForge Storage.

`POST /api/admin/backup` serializa todas las tablas (salvo embeddings, regenerables) a un JSON
comprimido y lo guarda en el bucket, con retención. Protegido por RUN_TOKEN. Pensado para un cron
diario. `GET /api/admin/backups` lista los backups existentes.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_api.config import settings
from tender_api.database import get_session
from tender_api.models import (
    Award,
    DailySnapshot,
    DraftGenJob,
    GeneratedDocument,
    RunLog,
    SavedAlert,
    ScoringProfile,
    Tender,
    TenderAction,
    TenderDecision,
    TenderDocument,
    TenderNote,
    TenderScore,
)
from tender_api.services import storage

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Se respaldan todas las tablas salvo TenderChunk (embeddings del pliego: grandes y regenerables).
_BACKUP_MODELS = [
    Tender, TenderScore, TenderDecision, TenderAction, GeneratedDocument, TenderDocument,
    Award, ScoringProfile, DailySnapshot, TenderNote, SavedAlert, DraftGenJob, RunLog,
]
_PREFIX = "backups/"
_RETENTION_DAYS = 30


def _require_run_token(x_run_token: str = Header(default="")) -> None:
    if not settings.run_token or x_run_token != settings.run_token:
        raise HTTPException(status_code=401, detail="RUN_TOKEN requerido.")


def _json_safe(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _serialize(session: Session) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for model in _BACKUP_MODELS:
        rows = session.scalars(select(model)).all()
        out[model.__tablename__] = [
            {c.name: _json_safe(getattr(r, c.name)) for c in r.__table__.columns} for r in rows
        ]
    return out


def _obj_key(obj: dict) -> str:
    return obj.get("key") or obj.get("name") or obj.get("object_key") or ""


def _prune_old() -> int:
    """Borra backups con fecha en el nombre anterior al periodo de retención."""
    cutoff = (datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)).strftime("%Y%m%d")
    removed = 0
    for obj in storage.list_objects(_PREFIX):
        key = _obj_key(obj)
        stamp = key.replace(_PREFIX, "").replace("tender-radar-", "")[:8]
        if stamp.isdigit() and stamp < cutoff:
            try:
                storage.delete(key)
                removed += 1
            except Exception:  # noqa: BLE001 — un borrado fallido no debe romper el backup
                pass
    return removed


@router.post("/backup", dependencies=[Depends(_require_run_token)])
def create_backup(session: Session = Depends(get_session)) -> dict:
    """Vuelca todas las tablas a un JSON.gz en el bucket. Uso interno (cron diario)."""
    if not storage.is_configured():
        raise HTTPException(status_code=503, detail="Almacenamiento no configurado.")
    tables = _serialize(session)
    payload = json.dumps(
        {"created_at": datetime.now(UTC).isoformat(), "tables": tables}, ensure_ascii=False
    ).encode("utf-8")
    blob = gzip.compress(payload)
    key = f"{_PREFIX}tender-radar-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json.gz"
    storage.put_bytes(key, blob, "application/gzip")
    return {
        "key": key,
        "tables": len(tables),
        "rows": sum(len(v) for v in tables.values()),
        "bytes": len(blob),
        "pruned": _prune_old(),
    }


@router.get("/backups", dependencies=[Depends(_require_run_token)])
def list_backups() -> dict:
    """Lista los backups existentes (clave y metadatos del bucket)."""
    if not storage.is_configured():
        raise HTTPException(status_code=503, detail="Almacenamiento no configurado.")
    objs = storage.list_objects(_PREFIX)
    return {"count": len(objs), "backups": objs}
