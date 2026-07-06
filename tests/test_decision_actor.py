from tests.conftest import make_tender


def test_decision_records_actor_in_activity(client):
    t = make_tender(client, source_id="ACT-1")
    r = client.post(
        f"/api/tenders/{t['id']}/decision",
        json={"decision": "GO", "outcome": "presentada", "actor": "R. Juárez"},
    )
    assert r.status_code == 201
    activity = client.get(f"/api/tenders/{t['id']}/activity").json()
    dec = next((e for e in activity if e["kind"] == "decision"), None)
    assert dec is not None
    assert dec["actor"] == "R. Juárez"
