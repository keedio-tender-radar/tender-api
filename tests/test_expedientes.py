from tests.conftest import make_tender


def test_expedientes_endpoint(client):
    r = client.get("/api/tenders/expedientes")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_expedientes_includes_seguimiento(client):
    t = make_tender(client)
    client.post(f"/api/tenders/{t['id']}/mark-interesting")
    data = client.get("/api/tenders/expedientes").json()
    row = next((x for x in data if x["tender"]["id"] == t["id"]), None)
    assert row is not None
    assert set(row["steps"]) == {"pliego", "borradores", "paquete"}
    assert row["completeness"] == 0  # recién marcado, sin pliego/borradores/paquete
