"""Gate de acceso al dashboard. Si `dashboard_password` está vacío, el gate está deshabilitado.

No protege la API a nivel de datos (los endpoints siguen abiertos para bot/crons); es un gate de
UI para la herramienta interna. Activar con DASHBOARD_PASSWORD en tender-api.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from tender_api.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def require_access(
    x_api_token: str = Header(default=""),
    x_run_token: str = Header(default=""),
) -> None:
    """Protege las lecturas de la API SI `read_api_token` está definido.

    Vacío → no exige nada (retrocompatible, API abierta). Definido → exige `X-Api-Token`
    (dashboard autenticado) o `X-Run-Token` (servicios internos: bot, análisis, crons).
    """
    token = settings.read_api_token
    if not token:
        return
    if x_api_token and x_api_token == token:
        return
    if settings.run_token and x_run_token == settings.run_token:
        return
    raise HTTPException(status_code=401, detail="Acceso no autorizado a la API.")


class CheckRequest(BaseModel):
    password: str = ""


@router.get("/status")
def status() -> dict:
    return {"enabled": bool(settings.dashboard_password)}


@router.post("/check")
def check(payload: CheckRequest) -> dict:
    ok = bool(settings.dashboard_password) and payload.password == settings.dashboard_password
    # Devuelve el token de API para que el dashboard autenticado lo mande en cada petición.
    return {"ok": ok, "token": settings.read_api_token if ok else ""}
