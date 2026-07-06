"""tender-api — API núcleo de Keedio Tender Radar."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tender_api.config import settings
from tender_api.database import init_db
from tender_api.routers import (
    actions,
    alerts,
    auth,
    market,
    profile,
    reports,
    runs,
    scores,
    tenders,
)
from tender_api.routers.auth import require_access


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Conveniencia MVP: crea tablas al arrancar. En producción se usa Alembic.
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Protección opt-in de la API (activa solo si READ_API_TOKEN está definido). auth y /health
# quedan siempre abiertos (el gate de acceso y el healthcheck deben ser accesibles).
_guard = [Depends(require_access)]
app.include_router(tenders.router, dependencies=_guard)
app.include_router(scores.router, dependencies=_guard)
app.include_router(actions.router, dependencies=_guard)
app.include_router(profile.router, dependencies=_guard)
app.include_router(auth.router)
app.include_router(runs.router, dependencies=_guard)
app.include_router(alerts.router, dependencies=_guard)
app.include_router(market.router, dependencies=_guard)
app.include_router(reports.router, dependencies=_guard)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "version": settings.version}
