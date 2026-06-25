import httpx

from tender_api.config import settings
from tender_api.services import analysis_client, doc_client
from tests.conftest import full_breakdown, make_tender


def _patch(monkeypatch):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")
    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    json={"chunks": [{"ordinal": 0, "section": None, "content": "datos api"}]},
                )
            ),
            base_url="http://doc",
        ),
    )

    def ai(r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "score": {
                    "total": 90,
                    "breakdown": full_breakdown(),
                    "recommendation": "go",
                    "hard_rules": [],
                    "factors": [],
                },
                "used_document": True,
            },
        )

    monkeypatch.setattr(
        analysis_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(ai), base_url="http://ai"),
    )


def _score(client, tid, rec):
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{tid}/score",
        json={"total": sum(bd.values()), "breakdown": bd, "recommendation": rec},
    )


def test_reanalyze_relevant_batch(client, monkeypatch):
    _patch(monkeypatch)
    a = make_tender(client, source_id="A", url="https://t/a")  # GO → candidato
    b = make_tender(client, source_id="B", url="https://t/b")  # no_go → no candidato
    _score(client, a["id"], "go")
    _score(client, b["id"], "no_go")

    resp = client.post("/api/tenders/reanalyze-relevant")
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"] == 1
    assert body["reanalyzed"] == 1
    # 'a' ahora tiene score basado en doc
    assert client.get(f"/api/tenders/{a['id']}/score").json()["model_version"] == "1.0.0+doc"


def test_reanalyze_relevant_token(client, monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(settings, "run_token", "secret")
    assert client.post("/api/tenders/reanalyze-relevant").status_code == 401
    ok = client.post(
        "/api/tenders/reanalyze-relevant", headers={"X-Run-Token": "secret"}
    )
    assert ok.status_code == 200
