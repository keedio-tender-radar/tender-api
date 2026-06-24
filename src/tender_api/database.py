from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tender_api.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict:
    # SQLite necesita check_same_thread=False para usarse con FastAPI/threads.
    if settings.database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(settings.database_url, connect_args=_connect_args(), future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Crea las tablas (conveniencia MVP; en producción se usa Alembic)."""
    from tender_api import models  # noqa: F401  (registra los modelos)

    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
