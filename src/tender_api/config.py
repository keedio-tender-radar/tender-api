from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de tender-api (variables de entorno / .env)."""

    app_name: str = "tender-api"
    version: str = "0.1.0"

    # Por defecto SQLite local; en despliegue, Postgres de tender-infra.
    database_url: str = "sqlite:///./tender.db"

    # Umbral de "urgente": días hasta el cierre para el endpoint /urgent.
    urgent_days: int = 7

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
