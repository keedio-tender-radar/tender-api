import httpx

from tender_api.config import settings
from tender_api.services import analysis_client
from tests.conftest import make_tender


def test_generate_and_list_offer_drafts(client, monkeypatch):
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")
    monkeypatch.setattr(settings, "doc_service_url", "")  # sin doc → document_text None

    def ai_handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/generate-drafts"
        # La API adjunta el contexto de mercado (MVP-5) para la estrategia de puja.
        import json

        assert "market_context" in json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "drafts": [
                    {"kind": "go_no_go", "title": "Informe Go/No-Go", "content": "# Go"},
                    {"kind": "memoria_tecnica", "title": "Memoria técnica", "content": "# Memoria"},
                ]
            },
        )

    monkeypatch.setattr(
        analysis_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(ai_handler), base_url="http://ai"),
    )

    t = make_tender(client)
    # Asíncrono: 202 + running; la tarea en segundo plano corre tras la respuesta (TestClient).
    resp = client.post(f"/api/tenders/{t['id']}/generate-offer-drafts")
    assert resp.status_code == 202
    assert resp.json()["status"] == "running"

    # El estado pasa a ok con la cuenta de borradores.
    status = client.get(f"/api/tenders/{t['id']}/offer-drafts-status").json()
    assert status["status"] == "ok"
    assert status["count"] == 2

    docs = client.get(f"/api/tenders/{t['id']}/generated-documents").json()
    kinds = {d["kind"] for d in docs}
    assert {"go_no_go", "memoria_tecnica"} == kinds
    assert any(d["content"] == "# Memoria" for d in docs)


def test_generate_drafts_503_without_analysis(client, monkeypatch):
    monkeypatch.setattr(settings, "analysis_service_url", "")
    t = make_tender(client)
    assert client.post(f"/api/tenders/{t['id']}/generate-offer-drafts").status_code == 503
