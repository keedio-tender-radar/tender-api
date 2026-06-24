# tender-api

> ⚙️ API núcleo de **Keedio Tender Radar**: modelo de datos, persistencia y endpoints para la
> ingesta, el scoring, las acciones humanas y el radar (bot/dashboard).

FastAPI + SQLAlchemy 2.0 + Alembic. Los formatos de datos provienen de
[`tender-shared-contracts`](https://github.com/keedio-tender-radar/tender-shared-contracts).

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Estado |
| POST | `/api/tenders` | Ingesta/alta (idempotente por `source`+`source_id`) |
| GET | `/api/tenders` | Listado (filtro `status`, `limit`) |
| GET | `/api/tenders/top` | Top por último score (para el radar) |
| GET | `/api/tenders/urgent` | Cierre dentro de `days` (def. `URGENT_DAYS`) |
| GET | `/api/tenders/{id}` | Ficha |
| PUT | `/api/tenders/{id}/score` | Alta de score Go/No-Go (marca `scored`) |
| GET | `/api/tenders/{id}/score` | Último score |
| POST | `/api/tenders/{id}/actions` | Registrar acción (cambia estado si procede) |
| GET | `/api/tenders/{id}/actions` | Historial de acciones |

## Modelo de datos (MVP)

`tenders` · `tender_scores` · `tender_actions` (documentos/chunks/análisis en fases siguientes).
Estados: discovered → screened → analyzed → scored → notified → interested/discarded/partner → go.

## Desarrollo

```bash
python -m venv .venv && . .venv/Scripts/activate    # Linux/mac: source .venv/bin/activate
pip install -e ../tender-shared-contracts            # contratos (repo hermano)
pip install fastapi "uvicorn[standard]" sqlalchemy alembic pydantic pydantic-settings \
            "psycopg[binary]" httpx pytest ruff

# Tests (SQLite en memoria, sin red)
pytest -q          # 16 tests
ruff check src tests

# Arranque (usa DATABASE_URL; por defecto SQLite local)
uvicorn tender_api.main:app --app-dir src --reload

# Migraciones (Postgres de tender-infra)
export DATABASE_URL=postgresql+psycopg://tender:tender@localhost:5432/tender
alembic upgrade head
```

## Notas

- **Idempotencia:** `POST /api/tenders` hace upsert por `(source, source_id)`.
- **Consistencia de score:** `PUT …/score` exige `total == suma(breakdown)` (422 si no).
- En arranque, la app crea las tablas (`create_all`) por conveniencia; en producción se usa
  **Alembic** (`alembic upgrade head`).
- `tender-contracts` es repo **privado**: en CI/Docker su instalación desde git requiere
  credenciales; en dev se instala en editable desde el repo hermano.
