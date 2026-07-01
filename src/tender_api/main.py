"""tender-api — API núcleo de Keedio Tender Radar."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tender_api.config import settings
from tender_api.database import init_db
from tender_api.routers import (
    actions,
    alerts,
    auth,
    market,
    profile,
    runs,
    scores,
    tenders,
)


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

app.include_router(tenders.router)
app.include_router(scores.router)
app.include_router(actions.router)
app.include_router(profile.router)
app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(alerts.router)
app.include_router(market.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "version": settings.version}
