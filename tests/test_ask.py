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


def test_rank_chunks_bm25_prefers_rare_term():
    from tender_api.routers.tenders import _rank_chunks

    # "solvencia" es raro (1 doc) → ese chunk debe rankear primero pese a que "el/de" son comunes.
    chunks = [
        {"content": "el objeto de la plataforma de datos y de la de gestion"},
        {"content": "el pliego exige solvencia tecnica de tres proyectos"},
        {"content": "el plazo de ejecucion de la de servicios"},
    ]
    top = _rank_chunks("¿qué solvencia técnica exige?", chunks, 1)
    assert "solvencia" in top[0]["content"]


def test_semantic_rank_by_embedding(monkeypatch):
    from tender_api.routers import tenders
    from tender_api.services import analysis_client

    # Pregunta ~ vector [1,0]; el chunk con embedding más alineado gana pese al léxico.
    monkeypatch.setattr(analysis_client, "embed", lambda texts: [[1.0, 0.0]])
    chunks = [
        {"content": "irrelevante", "embedding": [0.0, 1.0]},
        {"content": "relevante", "embedding": [0.9, 0.1]},
    ]
    top = tenders._semantic_rank("pregunta", chunks, 1)
    assert top[0]["content"] == "relevante"


def test_semantic_rank_none_without_embeddings(monkeypatch):
    from tender_api.routers import tenders

    assert tenders._semantic_rank("q", [{"content": "x"}], 1) is None


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
        import json

        if req.url.path == "/embed":  # embeddings desactivados en el test → BM25
            return httpx.Response(200, json={"embeddings": None})
        assert req.url.path == "/answer"
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


def test_reembed_chunks_backfills(client, monkeypatch):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")

    def doc_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PLIEGO_CHUNKS)

    def ai_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/embed":  # al construir: embeddings desactivados → chunks sin vector
            return httpx.Response(200, json={"embeddings": None})
        return httpx.Response(200, json={"answer": "x", "grounded": False, "generated_by": "t"})

    monkeypatch.setattr(
        doc_client, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(doc_handler), base_url="http://doc"),
    )
    monkeypatch.setattr(
        analysis_client, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(ai_handler), base_url="http://ai"),
    )
    t = make_tender(client, url="https://t/p.html")
    client.post(f"/api/tenders/{t['id']}/ask", json={"question": "solvencia"})  # construye chunks

    # Ahora los embeddings sí funcionan → el re-embed rellena los 2 chunks.
    monkeypatch.setattr(analysis_client, "embed", lambda texts: [[0.1, 0.2] for _ in texts])
    r = client.post("/api/tenders/reembed-chunks").json()
    assert r["embedded"] == 2
    assert r["remaining"] == 0


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
