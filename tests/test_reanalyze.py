import httpx

from tender_api.config import settings
from tender_api.services import analysis_client, doc_client
from tests.conftest import make_tender


def _patch(monkeypatch, doc_handler, analysis_handler):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")
    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(doc_handler), base_url="http://doc"),
    )
    monkeypatch.setattr(
        analysis_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(analysis_handler), base_url="http://ai"),
    )


def test_reanalyze_persists_score(client, monkeypatch):
    def doc_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"kind": "html", "char_count": 50, "chunk_count": 1,
                  "chunks": [{"ordinal": 0, "section": None, "content": "datos integración api"}]},
        )

    def ai_handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/analyze"
        return httpx.Response(
            200,
            json={
                "analysis": {"summary": "s", "generated_by": "rule-based"},
                "score": {
                    "total": 88,
                    "breakdown": {
                        "technical_fit": 30, "budget_fit": 13, "technical_solvency": 10,
                        "economic_solvency": 7, "deadline": 9, "partner_need": 5,
                        "documental_complexity": 4, "contractual_risk": 5,
                        "incompatibility_risk": 5,
                    },
                    "recommendation": "go",
                    "hard_rules": [],
                    "factors": [{"kind": "positive", "message": "keywords del pliego"}],
                },
                "used_document": True,
            },
        )

    _patch(monkeypatch, doc_handler, ai_handler)
    t = make_tender(client, url="https://ted.europa.eu/es/notice/x/html")
    resp = client.post(f"/api/tenders/{t['id']}/reanalyze")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 88
    assert body["model_version"] == "1.0.0+doc"
    # persistido: aparece como último score
    assert client.get(f"/api/tenders/{t['id']}/score").json()["total"] == 88


def test_reanalyze_without_url_422(client, monkeypatch):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")
    t = make_tender(client, url=None)
    assert client.post(f"/api/tenders/{t['id']}/reanalyze").status_code == 422
