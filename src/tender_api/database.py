from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tender_api.config import settings

# Columnas añadidas a tablas YA existentes: create_all no las crea, así que se añaden aquí
# de forma idempotente (Postgres soporta ADD COLUMN IF NOT EXISTS).
_COLUMN_MIGRATIONS = [
    ("tenders", "duplicate_of", "VARCHAR"),
    ("tender_scores", "summary", "VARCHAR"),
    ("scoring_profile", "team", "JSONB"),
    ("scoring_profile", "project_months", "INTEGER"),
    ("scoring_profile", "hourly_rate", "DOUBLE PRECISION"),
    ("scoring_profile", "margin", "DOUBLE PRECISION"),
    ("scoring_profile", "go_threshold", "INTEGER"),
    ("scoring_profile", "revisar_threshold", "INTEGER"),
    ("tenders", "document_text", "VARCHAR"),
    ("tenders", "document_extracted_at", "TIMESTAMPTZ"),
]


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
    """Crea las tablas (conveniencia MVP; en producción se usa Alembic).

    No es fatal: si la BD no está disponible al arrancar (blip transitorio), se registra y la
    app sigue arrancando — así un fallo de BD no provoca un crash-loop del contenedor.
    """
    import logging

    from tender_api import models  # noqa: F401  (registra los modelos)

    log = logging.getLogger("tender_api")
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # noqa: BLE001
        log.warning("init_db: create_all falló: %s", exc)

    # Auto-migración de columnas nuevas en tablas existentes (solo Postgres; SQLite ya las crea).
    if not settings.database_url.startswith("sqlite"):
        for table, column, coltype in _COLUMN_MIGRATIONS:
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}')
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("init_db: migración %s.%s falló: %s", table, column, exc)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
