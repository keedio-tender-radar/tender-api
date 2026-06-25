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


def ask(question: str, tender_id: str, top_k: int) -> dict:
    """Pregunta al pliego vía visual-rag. Devuelve la respuesta cruda del servicio."""
    with _client() as client:
        resp = client.post(
            "/ask", json={"question": question, "tender_id": tender_id, "top_k": top_k}
        )
        resp.raise_for_status()
        return resp.json()
