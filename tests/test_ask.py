import httpx

from tender_api.config import settings
from tender_api.services import analysis_client, doc_client, visual_rag_client
from tests.conftest import make_tender

_PLIEGO_CHUNKS = {
    "chunks": [
        {"ordinal": 0, "section": "Objeto", "content": "Plataforma de datos."},
        {
            "ordinal": 1,
            "section": "Solvencia",
            "content": "Se exige solvencia técnica con tres proyectos.",
        },
    ]
}


def test_ask_extractive_fallback(client, monkeypatch):
    # Sin visual-rag → QA extractivo sobre los chunks del doc-service.
    monkeypatch.setattr(settings, "visual_rag_url", "")
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")

    def doc_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chunks": [
                    {"ordinal": 0, "section": "Objeto", "content": "Plataforma de datos."},
                    {"ordinal": 1, "section": "Solvencia",
                     "content": "Se exige solvencia técnica con tres proyectos."},
                ]
            },
        )

    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(doc_handler), base_url="http://doc"),
    )
    t = make_tender(client, url="https://t/p.html")
    resp = client.post(
        f"/api/tenders/{t['id']}/ask", json={"question": "¿Qué solvencia técnica exige?"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "extractive"
    assert "solvencia" in body["answer"].lower()
    assert body["sources"][0]["section"] == "Solvencia"


def test_ask_uses_visual_rag_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "visual_rag_url", "http://vr")

    def vr_handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/ask"
        return httpx.Response(
            200, json={"answer": "Exige 3 proyectos similares.", "sources": [{"page": 4}]}
        )

    monkeypatch.setattr(
        visual_rag_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(vr_handler), base_url="http://vr"),
    )
    t = make_tender(client, url="https://t/p.html")
    body = client.post(f"/api/tenders/{t['id']}/ask", json={"question": "solvencia?"}).json()
    assert body["backend"] == "visual-rag"
    assert "3 proyectos" in body["answer"]
    assert body["sources"][0]["page"] == 4


def test_ask_503_when_nothing_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "visual_rag_url", "")
    monkeypatch.setattr(settings, "doc_service_url", "")
    t = make_tender(client, url="https://t/p.html")
    assert client.post(f"/api/tenders/{t['id']}/ask", json={"question": "x"}).status_code == 503


def test_ask_rag_synthesis_with_citations(client, monkeypatch):
    # doc-service + ai-analysis configurados, sin visual-rag → síntesis anclada con citas [n].
    monkeypatch.setattr(settings, "visual_rag_url", "")
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")

    def doc_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PLIEGO_CHUNKS)

    captured: dict = {}

    def ai_handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/answer"
        import json

        captured["chunks"] = json.loads(req.content)["chunks"]
        return httpx.Response(
            200,
            json={"answer": "Exige solvencia con 3 proyectos [2].", "grounded": True,
                  "generated_by": "llm:test"},
        )

    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(doc_handler), base_url="http://doc"),
    )
    monkeypatch.setattr(
        analysis_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(ai_handler), base_url="http://ai"),
    )
    t = make_tender(client, url="https://t/p.html")
    body = client.post(
        f"/api/tenders/{t['id']}/ask", json={"question": "¿Qué solvencia técnica exige?"}
    ).json()
    assert body["backend"] == "rag"
    assert body["grounded"] is True
    assert "[2]" in body["answer"]
    # Las fuentes van numeradas (para casar con las citas [n]).
    assert body["sources"][0]["n"] == 1
    assert captured["chunks"][0]["n"] == 1


def test_ask_caches_chunks_across_questions(client, monkeypatch):
    # La segunda pregunta reutiliza los tender_chunks cacheados: no re-extrae el pliego.
    monkeypatch.setattr(settings, "visual_rag_url", "")
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "")

    calls = {"n": 0}

    def doc_handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_PLIEGO_CHUNKS)

    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(doc_handler), base_url="http://doc"),
    )
    t = make_tender(client, url="https://t/p.html")
    client.post(f"/api/tenders/{t['id']}/ask", json={"question": "¿objeto?"})
    client.post(f"/api/tenders/{t['id']}/ask", json={"question": "¿solvencia?"})
    assert calls["n"] == 1
