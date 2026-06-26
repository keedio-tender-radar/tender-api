"""Gate de acceso al dashboard. Si `dashboard_password` está vacío, el gate está deshabilitado.

No protege la API a nivel de datos (los endpoints siguen abiertos para bot/crons); es un gate de
UI para la herramienta interna. Activar con DASHBOARD_PASSWORD en tender-api.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tender_api.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class CheckRequest(BaseModel):
    password: str = ""


@router.get("/status")
def status() -> dict:
    return {"enabled": bool(settings.dashboard_password)}


@router.post("/check")
def check(payload: CheckRequest) -> dict:
    ok = bool(settings.dashboard_password) and payload.password == settings.dashboard_password
    return {"ok": ok}
