from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de tender-api (variables de entorno / .env)."""

    app_name: str = "tender-api"
    version: str = "0.1.0"

    # Por defecto SQLite local; en despliegue, Postgres de tender-infra.
    database_url: str = "sqlite:///./tender.db"

    # Umbral de "urgente": días hasta el cierre para el endpoint /urgent.
    urgent_days: int = 7

    # CORS: orígenes permitidos para el dashboard (coma-separados).
    cors_origins: str = "http://localhost:3000,https://vz4wf92x.insforge.site"

    # tender-document-service (extracción de pliegos). Vacío = función deshabilitada.
    doc_service_url: str = ""

    # tender-ai-analysis-service (re-análisis con el pliego). Vacío = deshabilitado.
    analysis_service_url: str = ""
    analysis_token: str = ""  # X-Run-Token para /analyze si el servicio lo exige

    # tender-visual-rag (/ask). Vacío = fallback a QA extractivo sobre el pliego.
    visual_rag_url: str = ""
    visual_rag_token: str = ""

    # Token que protege endpoints batch para el scheduler (p. ej. reanalyze-relevant).
    run_token: str = ""

    # Almacenamiento de binarios del expediente (S3/MinIO). Vacío = deshabilitado (solo lógico).
    s3_endpoint_url: str = ""  # p. ej. https://minio.tu-infra:9000 (vacío = AWS S3)
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # Gate de acceso al dashboard. Vacío = sin gate (acceso libre, comportamiento actual).
    dashboard_password: str = ""

    # Logo de Keedio para los documentos generados (descargado y embebido; webp→png en runtime).
    keedio_logo_url: str = "https://keedio.com/images/Logo_keedio_hu_cd4a97c7d0e86e82.webp"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
