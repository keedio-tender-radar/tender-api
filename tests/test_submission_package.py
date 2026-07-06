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
    monkeypatch.setattr(settings, "insforge_api_url", "")
    monkeypatch.setattr(settings, "insforge_api_key", "")
    t = make_tender(client)
    r = client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("p.pdf", b"data", "application/pdf")},
    )
    assert r.status_code == 503
    assert client.get(f"/api/tenders/{t['id']}/documents").json()["files"] == []


def test_upload_list_download_delete_flow(client, monkeypatch):
    from tender_api.services import storage

    monkeypatch.setattr(settings, "insforge_api_url", "http://ins")
    monkeypatch.setattr(settings, "insforge_api_key", "k")
    store: dict[str, tuple[bytes, str]] = {}

    def _fake_put(key, data, ct="application/octet-stream"):
        store[key] = (data, ct)
        return key

    monkeypatch.setattr(storage, "put_bytes", _fake_put)
    monkeypatch.setattr(storage, "fetch", lambda key: store[key])
    monkeypatch.setattr(storage, "delete", lambda key: store.pop(key, None))

    t = make_tender(client)
    up = client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("pliego.pdf", b"PDFDATA", "application/pdf")},
        data={"folder": "00_originales"},
    )
    assert up.status_code == 201
    doc = up.json()
    assert doc["folder"] == "00_originales" and doc["filename"] == "pliego.pdf"

    files = client.get(f"/api/tenders/{t['id']}/documents").json()["files"]
    assert len(files) == 1 and files[0]["filename"] == "pliego.pdf"

    dl = client.get(f"/api/tenders/{t['id']}/documents/{doc['id']}/download")
    assert dl.status_code == 200 and dl.content == b"PDFDATA"

    bad = client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("virus.exe", b"x", "application/octet-stream")},
        data={"folder": "00_originales"},
    )
    assert bad.status_code == 400  # tipo no permitido

    assert client.delete(f"/api/tenders/{t['id']}/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/tenders/{t['id']}/documents").json()["files"] == []


def test_expediente_zip(client, monkeypatch):
    import io
    import zipfile

    from tender_api.services import storage

    monkeypatch.setattr(settings, "insforge_api_url", "http://ins")
    monkeypatch.setattr(settings, "insforge_api_key", "k")
    store: dict[str, tuple[bytes, str]] = {}

    def _fake_put(key, data, ct="application/octet-stream"):
        store[key] = (data, ct)
        return key

    monkeypatch.setattr(storage, "put_bytes", _fake_put)
    monkeypatch.setattr(storage, "fetch", lambda key: store[key])

    t = make_tender(client)
    client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("a.pdf", b"AAA", "application/pdf")},
        data={"folder": "00_originales"},
    )
    client.post(
        f"/api/tenders/{t['id']}/documents/upload",
        files={"file": ("b.md", b"BBB", "text/markdown")},
        data={"folder": "02_borradores_oferta"},
    )
    r = client.get(f"/api/tenders/{t['id']}/expediente.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"00_originales/a.pdf", "02_borradores_oferta/b.md"}
    assert z.read("00_originales/a.pdf") == b"AAA"
