from datetime import UTC, datetime, timedelta

from tests.conftest import make_tender


def test_notes(client):
    t = make_tender(client)
    assert client.post(f"/api/tenders/{t['id']}/notes", json={"body": ""}).status_code == 422
    r = client.post(
        f"/api/tenders/{t['id']}/notes", json={"body": "Revisar solvencia", "author": "RJ"}
    )
    assert r.status_code == 201
    notes = client.get(f"/api/tenders/{t['id']}/notes").json()
    assert notes[0]["body"] == "Revisar solvencia"
    assert notes[0]["author"] == "RJ"


def test_closing_soon(client):
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    far = (datetime.now(UTC) + timedelta(days=40)).isoformat()
    a = make_tender(client, source_id="A", deadline=soon)
    b = make_tender(client, source_id="B", deadline=far)
    client.post(f"/api/tenders/{a['id']}/actions", json={"action": "interested"})
    client.post(f"/api/tenders/{b['id']}/actions", json={"action": "interested"})
    ids = {x["tender"]["id"] for x in client.get("/api/tenders/closing-soon?days=7").json()}
    assert a["id"] in ids and b["id"] not in ids
    # tras marcar recordada, desaparece
    client.post(f"/api/tenders/{a['id']}/mark-reminded")
    ids2 = {x["tender"]["id"] for x in client.get("/api/tenders/closing-soon?days=7").json()}
    assert a["id"] not in ids2


def test_market_stats(client):
    make_tender(client, source_id="M1", buyer="Servicio de Salud", budget_amount=600000.0)
    make_tender(client, source_id="M2", buyer="Servicio de Salud", budget_amount=400000.0)
    m = client.get("/api/tenders/stats/market").json()
    assert m["top_buyers"]["Servicio de Salud"] == 2
    assert "placsp" in m["avg_budget_by_source"]
    assert m["by_month"]
