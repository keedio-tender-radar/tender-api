"""Cliente hacia tender-ai-analysis-service (POST /analyze) para re-puntuar con el pliego."""

from __future__ import annotations

import httpx

from tender_api.config import settings


def is_configured() -> bool:
    return bool(settings.analysis_service_url)


def _client() -> httpx.Client:
    """Cliente httpx hacia el servicio de análisis. Monkeypatcheable en tests."""
    headers = {}
    if settings.analysis_token:
        headers["X-Run-Token"] = settings.analysis_token
    return httpx.Client(
        base_url=settings.analysis_service_url.rstrip("/"), timeout=120, headers=headers
    )


def analyze(tender: dict, document_text: str | None) -> dict:
    """Devuelve {analysis, score, used_document} re-analizando la licitación con el pliego."""
    with _client() as client:
        resp = client.post("/analyze", json={"tender": tender, "document_text": document_text})
        resp.raise_for_status()
        return resp.json()


def generate_drafts(tender: dict, document_text: str | None, score: dict | None) -> dict:
    """Devuelve {drafts:[{kind,title,content}]} con los borradores de oferta."""
    with _client() as client:
        resp = client.post(
            "/generate-drafts",
            json={"tender": tender, "document_text": document_text, "score": score},
        )
        resp.raise_for_status()
        return resp.json()


def answer(question: str, chunks: list[dict]) -> dict:
    """Chat documental: síntesis anclada a los fragmentos.

    Devuelve {answer, grounded, generated_by}.
    """
    with _client() as client:
        resp = client.post("/answer", json={"question": question, "chunks": chunks})
        resp.raise_for_status()
        return resp.json()
