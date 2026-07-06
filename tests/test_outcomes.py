from tests.conftest import make_tender


def _decide(client, tid, decision, outcome, awarded=None):
    client.post(
        f"/api/tenders/{tid}/decision",
        json={"decision": decision, "outcome": outcome, "awarded_amount": awarded},
    )


def test_outcomes_summary_empty(client):
    r = client.get("/api/tenders/outcomes-summary")
    assert r.status_code == 200
    d = r.json()
    assert d["won"] == 0 and d["lost"] == 0 and d["win_rate"] is None


def test_outcomes_summary_win_rate_and_value(client):
    a = make_tender(client, source_id="OUT-A")
    b = make_tender(client, source_id="OUT-B")
    c = make_tender(client, source_id="OUT-C")
    _decide(client, a["id"], "GO", "ganada", awarded=120000)
    _decide(client, b["id"], "GO", "perdida")
    _decide(client, c["id"], "GO", "presentada")
    d = client.get("/api/tenders/outcomes-summary").json()
    assert d["won"] == 1 and d["lost"] == 1
    assert d["win_rate"] == 50  # 1 de 2 decididas
    assert d["presented"] == 3  # ganada + perdida + presentada
    assert d["won_value"] == 120000
    assert d["by_outcome"]["ganada"] == 1


def test_outcomes_summary_uses_latest_decision(client):
    t = make_tender(client)
    _decide(client, t["id"], "GO", "presentada")
    _decide(client, t["id"], "GO", "ganada", awarded=50000)  # re-registro posterior
    d = client.get("/api/tenders/outcomes-summary").json()
    assert d["won"] == 1 and d["total_decisions"] == 1  # solo la última cuenta
