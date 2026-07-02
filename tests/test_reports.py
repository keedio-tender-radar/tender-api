from tender_api.config import settings
from tender_api.services import email
from tests.conftest import make_tender


def test_email_digest_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "digest_email_to", "")
    assert client.post("/api/reports/email-digest").status_code == 503


def test_email_digest_sends(client, monkeypatch):
    monkeypatch.setattr(settings, "insforge_api_url", "https://x.insforge.app")
    monkeypatch.setattr(settings, "insforge_anon_key", "anon")
    monkeypatch.setattr(settings, "digest_email_to", "dir@keedio.com")

    captured = {}

    def fake_send(subject, html_body, to=None):
        captured["subject"] = subject
        captured["html"] = html_body
        return {"id": "e1"}

    monkeypatch.setattr(email, "send", fake_send)

    make_tender(client)  # hay al menos una licitación
    resp = client.post("/api/reports/email-digest")
    assert resp.status_code == 200
    assert resp.json()["sent_to"] == ["dir@keedio.com"]
    assert "Keedio Tender Radar" in captured["html"]
