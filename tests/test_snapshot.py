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
