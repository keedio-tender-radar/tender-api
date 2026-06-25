"""Cliente hacia tender-document-service (extracción de pliegos).

La API orquesta la llamada server-side (sin CORS); el dashboard solo habla con tender-api.
"""

from __future__ import annotations

import httpx

from tender_api.config import settings


def is_configured() -> bool:
    return bool(settings.doc_service_url)


def _client() -> httpx.Client:
    """Cliente httpx hacia el doc-service. Monkeypatcheable en tests."""
    return httpx.Client(base_url=settings.doc_service_url.rstrip("/"), timeout=90)


def extract(url: str) -> dict:
    """Descarga y extrae el documento en `url`. Devuelve {kind, char_count, chunk_count, chunks}."""
    with _client() as client:
        resp = client.post("/extract", json={"url": url})
        resp.raise_for_status()
        return resp.json()
