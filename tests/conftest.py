import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tender_api.database import Base, get_session
from tender_api.main import app


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_session] = override
    # Las tareas en segundo plano (p. ej. generación de borradores) abren su propia sesión con
    # SessionLocal; apuntarla al motor de test para que escriban en la misma BD en memoria.
    import tender_api.routers.tenders as _tenders
    _old_session_local = _tenders.SessionLocal
    _tenders.SessionLocal = TestingSession
    with TestClient(app) as client:
        yield client
    _tenders.SessionLocal = _old_session_local
    app.dependency_overrides.clear()


def make_tender(client, **overrides) -> dict:
    body = {
        "source": "placsp",
        "source_id": "PLACSP-2026-000184",
        "title": "Plataforma de datos sanitarios",
        "cpv": ["72300000"],
        "budget_amount": 620000.0,
        "deadline": "2026-07-15T23:59:00Z",
    }
    body.update(overrides)
    resp = client.post("/api/tenders", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def full_breakdown(total_dims: dict | None = None) -> dict:
    base = {
        "technical_fit": 29,
        "budget_fit": 13,
        "technical_solvency": 14,
        "economic_solvency": 9,
        "deadline": 9,
        "partner_need": 5,
        "documental_complexity": 4,
        "contractual_risk": 4,
        "incompatibility_risk": 5,
    }
    if total_dims:
        base.update(total_dims)
    return base
