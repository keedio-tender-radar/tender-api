from tests.conftest import make_tender


def test_action_changes_status(client):
    t = make_tender(client)
    resp = client.post(
        f"/api/tenders/{t['id']}/actions",
        json={"action": "interested", "actor": "telegram:123"},
    )
    assert resp.status_code == 201
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "interested"


def test_discard_and_partner(client):
    t = make_tender(client)
    client.post(f"/api/tenders/{t['id']}/actions", json={"action": "discarded"})
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "discarded"
    client.post(f"/api/tenders/{t['id']}/actions", json={"action": "partner"})
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "partner"


def test_non_status_action_keeps_status(client):
    t = make_tender(client)
    client.post(f"/api/tenders/{t['id']}/actions", json={"action": "prioritize"})
    # 'prioritize' no cambia el estado del ciclo de vida
    assert client.get(f"/api/tenders/{t['id']}").json()["status"] == "discovered"


def test_invalid_action_422(client):
    t = make_tender(client)
    assert (
        client.post(f"/api/tenders/{t['id']}/actions", json={"action": "bailar"}).status_code
        == 422
    )


def test_list_actions(client):
    t = make_tender(client)
    client.post(f"/api/tenders/{t['id']}/actions", json={"action": "interested"})
    client.post(f"/api/tenders/{t['id']}/actions", json={"action": "prioritize"})
    actions = client.get(f"/api/tenders/{t['id']}/actions").json()
    assert len(actions) == 2


def test_action_unknown_tender_404(client):
    assert (
        client.post("/api/tenders/nope/actions", json={"action": "interested"}).status_code == 404
    )
