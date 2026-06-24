FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# Dependencias. tender-contracts se instala desde git (repo de la organización).
COPY pyproject.toml .
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy>=2.0" "alembic>=1.13" \
      "pydantic>=2.6" "pydantic-settings>=2.5" "psycopg[binary]>=3.2" \
      "tender-contracts @ git+https://github.com/keedio-tender-radar/tender-shared-contracts.git"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "tender_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src"]
