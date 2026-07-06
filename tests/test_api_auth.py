from tender_api.config import settings


def test_api_open_when_token_unset(client):
    # Retrocompatible: sin READ_API_TOKEN la API sigue abierta.
    assert client.get("/api/tenders/stats").status_code == 200


def test_api_requires_token_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "read_api_token", "SEKRET")
    monkeypatch.setattr(settings, "run_token", "RUNTOK")

    assert client.get("/api/tenders/stats").status_code == 401
    assert (
        client.get("/api/tenders/stats", headers={"X-Api-Token": "SEKRET"}).status_code == 200
    )
    assert (
        client.get("/api/tenders/stats", headers={"X-Run-Token": "RUNTOK"}).status_code == 200
    )
    assert (
        client.get("/api/tenders/stats", headers={"X-Api-Token": "nope"}).status_code == 401
    )


def test_health_and_auth_always_open(client, monkeypatch):
    monkeypatch.setattr(settings, "read_api_token", "SEKRET")
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200


def test_auth_check_returns_token(client, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_password", "pw")
    monkeypatch.setattr(settings, "read_api_token", "SEKRET")
    body = client.post("/api/auth/check", json={"password": "pw"}).json()
    assert body["ok"] is True and body["token"] == "SEKRET"
    bad = client.post("/api/auth/check", json={"password": "x"}).json()
    assert bad["ok"] is False and bad["token"] == ""
