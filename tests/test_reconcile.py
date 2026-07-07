from tender_api.config import settings
from tests.conftest import make_tender


def _present(client, tid):
    client.post(
        f"/api/tenders/{tid}/decision",
        json={"decision": "GO", "outcome": "presentada"},
    )


def _award(client, source_id, buyer, title, supplier, amount=90000):
    client.post(
        "/api/market/awards",
        json=[{
            "source": "placsp", "source_id": source_id, "buyer": buyer,
            "title": title, "awarded_supplier": supplier, "awarded_amount": amount,
        }],
        headers={"X-Run-Token": "RT"},  # la ingesta de awards exige RUN_TOKEN
    )


def test_reconcile_requires_token(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    assert client.post("/api/market/reconcile-outcomes").status_code == 401


def test_reconcile_marks_lost_when_other_wins(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    t = make_tender(
        client, source_id="REC-1",
        buyer="Ayuntamiento de Getafe", title="Servicios de plataforma de datos",
    )
    _present(client, t["id"])
    _award(client, "AW-1", "Ayuntamiento de Getafe",
           "Servicios de plataforma de datos corporativa", "Otra Empresa SL")

    r = client.post("/api/market/reconcile-outcomes", headers={"X-Run-Token": "RT"}).json()
    assert r["matched"] == 1 and r["perdidas"] == 1 and r["ganadas"] == 0
    assert client.get("/api/tenders/outcomes-summary").json()["lost"] == 1
    # Idempotente: una segunda pasada no vuelve a emparejar (ya resuelta).
    r2 = client.post("/api/market/reconcile-outcomes", headers={"X-Run-Token": "RT"}).json()
    assert r2["matched"] == 0


def test_reconcile_detects_keedio_win(client, monkeypatch):
    monkeypatch.setattr(settings, "run_token", "RT")
    monkeypatch.setattr(settings, "keedio_supplier_names", "keedio")
    t = make_tender(
        client, source_id="REC-2",
        buyer="Diputacion Provincial", title="Consultoria de datos e inteligencia artificial",
    )
    _present(client, t["id"])
    _award(client, "AW-2", "Diputacion Provincial",
           "Consultoria de datos e inteligencia artificial avanzada", "Keedio SL", amount=100000)

    r = client.post("/api/market/reconcile-outcomes", headers={"X-Run-Token": "RT"}).json()
    assert r["ganadas"] == 1 and r["perdidas"] == 0
