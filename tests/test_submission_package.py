import httpx

from tender_api.config import settings
from tender_api.services import analysis_client
from tests.conftest import make_tender


def _mock_ai(monkeypatch):
    monkeypatch.setattr(settings, "analysis_service_url", "http://ai")
    monkeypatch.setattr(settings, "doc_service_url", "")

    def ai(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"drafts": [
                {"kind": "go_no_go", "title": "Go/No-Go", "content": "# Go"},
                {"kind": "memoria_tecnica", "title": "Memoria", "content": "# Mem"},
                {"kind": "checklist_administrativo", "title": "Checklist", "content": "# Chk"},
            ]},
        )

    monkeypatch.setattr(
        analysis_client, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(ai), base_url="http://ai"),
    )


def test_prepare_submission_package(client, monkeypatch):
    _mock_ai(monkeypatch)
    t = make_tender(client)
    body = client.post(f"/api/tenders/{t['id']}/prepare-submission-package").json()
    assert body["documents"] == 3
    assert "memoria_tecnica.md" in body["package"]["04_tecnico"]
    assert "checklist_administrativo.md" in body["package"]["03_administrativo"]
    assert body["pending_human"]
    assert "Expediente" in body["manifest_md"]
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "interested"


def test_upload_503_without_storage(client, monkeypatch):
    monkeypatch.setattr(settings, "s3_bucket", "")
    t = make_tender(client)
    r = client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("p.pdf", b"data", "application/pdf")},
    )
    assert r.status_code == 503
    assert client.get(f"/api/tenders/{t['id']}/documents").json() == []
