from datetime import UTC, datetime, timedelta

from tests.conftest import full_breakdown, make_tender


def test_daily_snapshot(client):
    future = (datetime.now(UTC) + timedelta(days=20)).isoformat()
    t = make_tender(client, source_id="S1", deadline=future)
    bd = full_breakdown()
    client.put(
        f"/api/tenders/{t['id']}/score",
        json={"total": sum(bd.values()), "breakdown": bd, "recommendation": "go"},
    )
    # crear snapshot
    snap = client.post("/api/tenders/daily-snapshot").json()
    assert snap["count"] == 1
    assert snap["number"] == 1
    assert snap["items"][0]["tender_id"] == t["id"]
    assert snap["items"][0]["traffic_light"] == "green"
    # idempotente por fecha: re-crear no duplica
    snap2 = client.post("/api/tenders/daily-snapshot").json()
    assert snap2["number"] == 1
    # GET última
    latest = client.get("/api/tenders/daily-snapshot").json()
    assert latest["count"] == 1 and latest["date"] == snap["date"]


def test_daily_snapshot_token(client, monkeypatch):
    from tender_api.config import settings
    monkeypatch.setattr(settings, "run_token", "secret")
    assert client.post("/api/tenders/daily-snapshot").status_code == 401
    assert client.post(
        "/api/tenders/daily-snapshot", headers={"X-Run-Token": "secret"}
    ).status_code == 200


def test_daily_snapshots_history(client):
    from datetime import UTC, datetime, timedelta
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    t = make_tender(client, source_id="H1", deadline=future)
    bd = full_breakdown()
    client.put(f"/api/tenders/{t['id']}/score",
               json={"total": sum(bd.values()), "breakdown": bd, "recommendation": "go"})
    client.post("/api/tenders/daily-snapshot")
    hist = client.get("/api/tenders/daily-snapshots?limit=14").json()
    assert len(hist) >= 1
    assert hist[0]["count"] >= 1 and "items" in hist[0]


def test_market_csv(client):
    make_tender(client, source_id="MC1", buyer="Diputacion", budget_amount=300000.0)
    r = client.get("/api/tenders/stats/market.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "tipo;clave;valor" in r.text
