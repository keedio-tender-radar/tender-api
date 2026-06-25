import httpx

from tender_api.config import settings
from tender_api.services import doc_client
from tests.conftest import make_tender


def _patch_doc(monkeypatch, handler):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    monkeypatch.setattr(
        doc_client,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://doc"),
    )


def test_extract_proxies_to_doc_service(client, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/extract"
        return httpx.Response(
            200,
            json={
                "kind": "html",
                "char_count": 120,
                "chunk_count": 2,
                "chunks": [{"ordinal": 0, "section": "Objeto", "content": "texto"}],
            },
        )

    _patch_doc(monkeypatch, handler)
    t = make_tender(client, url="https://ted.europa.eu/es/notice/430921-2026/html")
    resp = client.post(f"/api/tenders/{t['id']}/extract")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "html"
    assert resp.json()["chunk_count"] == 2


def test_extract_without_url_422(client, monkeypatch):
    monkeypatch.setattr(settings, "doc_service_url", "http://doc")
    t = make_tender(client, url=None)
    assert client.post(f"/api/tenders/{t['id']}/extract").status_code == 422


def test_extract_not_configured_503(client, monkeypatch):
    monkeypatch.setattr(settings, "doc_service_url", "")
    t = make_tender(client, url="https://x/doc.html")
    assert client.post(f"/api/tenders/{t['id']}/extract").status_code == 503
