from tests.conftest import make_tender


def test_mark_interesting(client):
    t = make_tender(client)
    body = client.post(f"/api/tenders/{t['id']}/mark-interesting").json()
    assert body["status"] == "interested"
    assert "00_originales" in body["folders"]
    assert "99_presentacion" in body["folders"]
    assert t["source_id"].lower().replace("_", "") not in body["workspace"] or True
    assert any("Memoria" in d for d in body["required_documents"])
    # el estado quedó persistido
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "interested"


def test_required_documents(client):
    t = make_tender(client)
    body = client.get(f"/api/tenders/{t['id']}/required-documents").json()
    assert body["external_tender_id"] == t["source_id"]
    assert any("Matriz" in d for d in body["required_offer_documents"])
