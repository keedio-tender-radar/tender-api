from tests.conftest import make_tender


def test_runs_log_and_summary(client):
    r = client.post("/api/runs", json={"job": "ingesta", "status": "ok", "count": 12})
    assert r.status_code == 201
    runs = client.get("/api/runs").json()
    assert any(x["job"] == "ingesta" and x["status"] == "ok" for x in runs)
    summ = client.get("/api/runs/summary").json()
    assert any(j["job"] == "ingesta" for j in summ["jobs"])


def test_calendar_ics(client):
    make_tender(client, deadline="2030-01-01T10:00:00+00:00")
    r = client.get("/api/tenders/calendar.ics")
    assert r.status_code == 200
    assert "text/calendar" in r.headers["content-type"]
    body = r.text
    assert body.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in body


def test_services_status(client):
    r = client.get("/api/tenders/services")
    assert r.status_code == 200
    j = r.json()
    assert "doc_service" in j and "analysis_service" in j and "llm" in j


def test_recalibrate_insufficient(client):
    r = client.post("/api/profile/recalibrate")
    assert r.status_code == 200
    assert r.json()["applied"] is False  # sin decisiones ganadas


def test_profile_has_thresholds(client):
    p = client.get("/api/profile").json()
    assert p["go_threshold"] == 80
    assert p["revisar_threshold"] == 40
