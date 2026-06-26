FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app/vendor

# tender-contracts se VENDORIZA en ./vendor (repo privado → no se instala desde git en el build).
# Generar antes del deploy con: bash scripts/vendor-contracts.sh  (copia el paquete a ./vendor).
COPY pyproject.toml .
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy>=2.0" "alembic>=1.13" \
      "pydantic>=2.6" "pydantic-settings>=2.5" "psycopg[binary]>=3.2" "httpx>=0.27" \
      "python-multipart>=0.0.9" "boto3>=1.34"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "tender_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src"]
