from tender_api.config import settings
from tests.conftest import make_tender


def test_auth_disabled_by_default(client):
    assert client.get("/api/auth/status").json()["enabled"] is False
    assert client.post("/api/auth/check", json={"password": "x"}).json()["ok"] is False


def test_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_password", "secreto")
    assert client.get("/api/auth/status").json()["enabled"] is True
    assert client.post("/api/auth/check", json={"password": "secreto"}).json()["ok"] is True
    assert client.post("/api/auth/check", json={"password": "mal"}).json()["ok"] is False


def test_package_md_download(client):
    t = make_tender(client)
    r = client.get(f"/api/tenders/{t['id']}/package.md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Paquete de oferta" in r.text


def test_analysis_summary_endpoint(client):
    t = make_tender(client)
    bd = {
        "technical_fit": 29, "budget_fit": 13, "technical_solvency": 10, "economic_solvency": 7,
        "deadline": 9, "partner_need": 5, "documental_complexity": 4, "contractual_risk": 5,
        "incompatibility_risk": 5,
    }
    client.put(
        f"/api/tenders/{t['id']}/score",
        json={"total": sum(bd.values()), "breakdown": bd, "recommendation": "go",
              "summary": "Encaje alto en datos e integración."},
    )
    body = client.get(f"/api/tenders/{t['id']}/analysis").json()
    assert body["summary"] == "Encaje alto en datos e integración."
    assert body["recommendation"] == "go"
