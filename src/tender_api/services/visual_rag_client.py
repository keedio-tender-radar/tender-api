"""Cliente hacia tender-visual-rag (POST /ask) — RAG visual del pliego por expediente."""

from __future__ import annotations

import httpx

from tender_api.config import settings


def is_configured() -> bool:
    return bool(settings.visual_rag_url)


def _client() -> httpx.Client:
    """Cliente httpx hacia el servicio visual-rag. Monkeypatcheable en tests."""
    headers = {}
    if settings.visual_rag_token:
        headers["X-Run-Token"] = settings.visual_rag_token
    return httpx.Client(
        base_url=settings.visual_rag_url.rstrip("/"), timeout=120, headers=headers
    )


def ask(question: str, tender_id: str, top_k: int, document_text: str | None = None) -> dict:
    """Pregunta al pliego vía visual-rag. Devuelve la respuesta cruda del servicio.

    `document_text` permite que el backend indice al vuelo (scaffold) o lo ignore si ya
    pre-ingestó el expediente (backend real PixelRAG).
    """
    payload: dict = {"question": question, "tender_id": tender_id, "top_k": top_k}
    if document_text:
        payload["document_text"] = document_text
    with _client() as client:
        resp = client.post("/ask", json=payload)
        resp.raise_for_status()
        return resp.json()
