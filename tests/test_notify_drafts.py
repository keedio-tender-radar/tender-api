from types import SimpleNamespace

from tender_api.config import settings
from tender_api.routers import runs, tenders


def test_notify_drafts_ready_sends_message_with_link(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(runs, "_notify_telegram", lambda text: sent.append(text))
    monkeypatch.setattr(settings, "dashboard_url", "https://dash")

    tender = SimpleNamespace(id="t1", title="Servicios de soporte")
    tenders._notify_drafts_ready(tender, 9)

    assert sent and "Oferta lista: 9 borradores" in sent[0]
    assert "«Servicios de soporte»" in sent[0]
    assert "https://dash/tenders/t1" in sent[0]


def test_notify_drafts_ready_without_dashboard_url(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(runs, "_notify_telegram", lambda text: sent.append(text))
    monkeypatch.setattr(settings, "dashboard_url", "")

    tenders._notify_drafts_ready(SimpleNamespace(id="t2", title="X"), 5)
    assert sent and "/tenders/" not in sent[0]  # sin URL → sin enlace
